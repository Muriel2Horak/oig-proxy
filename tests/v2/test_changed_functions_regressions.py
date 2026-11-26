# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "quality"))

import changed_functions as check  # pylint: disable=import-error,wrong-import-position


class Regressions(unittest.TestCase):
    def test_else_if_is_not_elif(self):
        node = ast.parse('def f(x):\n    if x:\n        pass\n    else:\n        if x:\n            pass\n').body[0]
        self.assertEqual(check.cognitive_complexity(node), 4)

    def test_wrapper_does_not_add_nesting(self):
        node = ast.parse('def f():\n    def wrapper():\n        def inner():\n            if x:\n                pass\n        return inner\n    return wrapper\n').body[0]
        self.assertEqual(check.cognitive_complexity(node), 1)

    def test_modified_below_threshold_is_ratcheted(self):
        old = check.analyze_module('def f(a):\n    return a\n')[0]
        new = check.analyze_module('def f(a,b):\n    if a:\n        return b\n    return 0\n')[0]
        self.assertEqual({f.rule for f in check.check_unit('x.py', new, [old])}, {'S3776', 'S107'})

    def test_deletion_is_recorded_at_preceding_head_line(self):
        self.assertEqual(check._hunk_lines('@@ -2,1 +1,0 @@'), {1})
        self.assertEqual(check._hunk_lines('@@ -1,1 +0,0 @@'), set())

    def test_decorator_header_and_body_all_select(self):
        source = '@decorator\ndef f(\n    a,\n):\n    # comment\n    return a\n'
        unit = check.analyze_module(source)[0]
        self.assertTrue(all(check._selected(unit, n) for n in (1, 2, 3, 4, 5, 6)))

class ConditionalRegressions(unittest.TestCase):
    def test_replacing_a_nested_conditional_is_a_new_finding(self):
        old = check.analyze_module('def f(a,b):\n    x = a if b else (2 if a else 3)\n    y = a if b else 0\n    return x + y\n')[0]
        new = check.analyze_module('def f(a,b):\n    x = a if b else 2\n    y = a if b else (b if a else 0)\n    return x + y\n')[0]
        self.assertIn('S3358', {f.rule for f in check.check_unit('x.py', new, [old])})

    def test_nested_finding_belongs_to_inner_function(self):
        units = check.analyze_module('def outer(a,b):\n    def inner(x,y):\n        return x if y else (y if x else 0)\n    return inner(a,b)\n')
        self.assertNotIn('S3358', {f.rule for f in check.check_unit('x.py', units[0], None)})
        self.assertIn('S3358', {f.rule for f in check.check_unit('x.py', units[1], None)})

class ConditionalIdentityRegressions(unittest.TestCase):
    def test_unchanged_nested_expression_survives_surrounding_edit(self):
        old = check.analyze_module('def f(a,b):\n    return a if b else (b if a else 0)\n')[0]
        new = check.analyze_module('def f(a,b):\n    return 2 if b else (b if a else 0)\n')[0]
        self.assertNotIn('S3358', {f.rule for f in check.check_unit('x.py', new, [old])})

    def test_assignment_target_rename_keeps_existing_finding(self):
        old = check.analyze_module('def f(a,b):\n    x = a if b else (b if a else 0)\n    return x\n')[0]
        new = check.analyze_module('def f(a,b):\n    y = a if b else (b if a else 0)\n    return y\n')[0]
        self.assertNotIn('S3358', {f.rule for f in check.check_unit('x.py', new, [old])})

    def test_annotated_and_augmented_assignment_renames(self):
        for before, after in [('x: int =', 'y: int ='), ('x +=', 'y +=')]:
            with self.subTest(before=before):
                old = check.analyze_module(f'def f(a,b):\n    {before} a if b else (b if a else 0)\n')[0]
                new = check.analyze_module(f'def f(a,b):\n    {after} a if b else (b if a else 0)\n')[0]
                self.assertNotIn('S3358', {f.rule for f in check.check_unit('x.py', new, [old])})

    def test_renames_with_repeated_values_and_existing_targets(self):
        old = check.analyze_module('def f(a,b):\n    x = a if b else (b if a else 0)\n    z = a if b else (b if a else 0)\n    x = 1\n    y = 2\n    return x + y + z\n')[0]
        new = check.analyze_module('def f(a,b):\n    y = a if b else (b if a else 0)\n    w = a if b else (b if a else 0)\n    x = 1\n    y = 2\n    return x + y + w\n')[0]
        self.assertNotIn('S3358', {f.rule for f in check.check_unit('x.py', new, [old])})

    def test_moving_existing_expression_to_another_assignment_is_new(self):
        old = check.analyze_module('def f(a,b):\n    x = a if b else (b if a else 0)\n    y = 0\n    return x + y\n')[0]
        new = check.analyze_module('def f(a,b):\n    x = 0\n    y = a if b else (b if a else 0)\n    return x + y\n')[0]
        self.assertIn('S3358', {f.rule for f in check.check_unit('x.py', new, [old])})

    def test_new_nested_conditional_after_rename_is_reported(self):
        old = check.analyze_module('def f(a,b):\n    x = a if b else 0\n    return x\n')[0]
        new = check.analyze_module('def f(a,b):\n    y = a if b else (b if a else 0)\n    return y\n')[0]
        self.assertIn('S3358', {f.rule for f in check.check_unit('x.py', new, [old])})


class BomRegressions(unittest.TestCase):
    def test_bom_changed_function_and_new_violation(self):
        with tempfile.TemporaryDirectory() as repo:
            def git(*args):
                return subprocess.run(['git', '-C', repo, *args], check=True, capture_output=True, text=True).stdout.strip()

            Path(repo, '.sonar').mkdir()
            Path(repo, '.sonar', 'local.properties').write_text('sonar.sources=.\n', encoding='utf-8')
            git('init', '-q')
            git('config', 'user.email', 'test@example.invalid')
            git('config', 'user.name', 'Test')
            source = Path(repo) / 'example.py'
            source.write_bytes(b'\xef\xbb\xbfdef f(a,b):\n    return a\n')
            git('add', 'example.py')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            source.write_bytes(b'\xef\xbb\xbfdef f(a,b):\n    return a if b else (b if a else 0)\n')
            git('add', 'example.py')
            git('commit', '-qm', 'change')
            warnings = []
            findings = check.check_diff(repo, base, warn=warnings.append)
            self.assertFalse(warnings)
            self.assertIn(('f', 'S3358'), {(f.qualname, f.rule) for f in findings})

            source.write_bytes(b'\xef\xbb\xbfdef f(a,b):\n    return 2 if b else (b if a else 0)\n')
            git('add', 'example.py')
            git('commit', '-qm', 'surrounding edit')
            findings = check.check_diff(repo, 'HEAD^', warn=warnings.append)
            self.assertFalse(warnings)
            self.assertNotIn('S3358', {f.rule for f in findings})

class BooleanSequenceRegressions(unittest.TestCase):
    def score(self, expr, parenthesized_source=None):
        source = 'def f(a, b, c, d, e):\n    return ' + expr + '\n'
        return check.analyze_module(source)[0].metrics['Q1']

    def test_bare_and_in_middle_of_or_chain_counts_or_twice(self):
        self.assertEqual(self.score('a or b and c or d'), 3)  # sonar: or, and, or

    def test_bare_and_at_either_end_of_or_chain_counts_once_each(self):
        self.assertEqual(self.score('a and b or c'), 2)
        self.assertEqual(self.score('a or b and c'), 2)

    def test_parenthesized_and_in_middle_is_a_separate_sequence(self):
        self.assertEqual(self.score('a or (b and c) or d'), 2)
        self.assertEqual(self.score('a or (\n  # (\n  b and c) or d'), 2)

    def test_two_bare_ands_in_the_middle_each_split_the_chain(self):
        self.assertEqual(self.score('a or b and c or d and e or a'), 5)

    def test_bare_and_after_parenthesized_operand_still_splits(self):
        self.assertEqual(self.score('(a) or b and c or (d)'), 3)

    def test_multiline_and_with_comment_and_non_ascii_text(self):
        self.assertEqual(self.score('a or \\\n    b and c or "é" # note ( here'), 3)


class ScopeRegressions(unittest.TestCase):
    def scope(self, text):
        with tempfile.TemporaryDirectory() as repo:
            Path(repo, '.sonar').mkdir()
            Path(repo, '.sonar', 'local.properties').write_text(text, encoding='utf-8')
            return check.load_scope(repo)

    def test_properties_comments_continuations_and_lists(self):
        scope = self.scope('# c\nsonar.sources=src, lib\nsonar.tests = tests\nsonar.exclusions=a/**,\\\n  b/*.py\n')
        self.assertTrue(scope.is_main('src/a.py'))
        self.assertTrue(scope.is_main('lib/x/a.py'))
        self.assertFalse(scope.is_main('other/a.py'))
        self.assertFalse(scope.is_main('src/a.txt'))
        self.assertFalse(scope.is_main('tests/a.py'))

    def test_exclusions_and_test_inclusions(self):
        scope = self.scope('sonar.sources=.\nsonar.exclusions=gen/**,x/*.py\nsonar.test.inclusions=**/test_*.py\n')
        self.assertFalse(scope.is_main('gen/deep/a.py'))
        self.assertFalse(scope.is_main('x/a.py'))
        self.assertTrue(scope.is_main('x/y/a.py'))
        self.assertFalse(scope.is_main('pkg/test_a.py'))
        self.assertTrue(scope.is_main('pkg/a.py'))

    def test_missing_sources_is_an_error(self):
        with self.assertRaises(check.ScopeError):
            self.scope('sonar.tests=tests\n')

    def test_missing_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as repo:
            with self.assertRaises(check.ScopeError):
                check.load_scope(repo)


if __name__ == '__main__':
    unittest.main()
