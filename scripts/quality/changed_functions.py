# Vendored from agent-ops 8c446efc5 scripts/quality/changed_functions.py; scope from .sonar/local.properties
"""Sonar-aligned quality checks for changed Python functions (stdlib only).

Rules mirrored (Sonar profile "Sonar agentic AI"):
- Q1 python:S3776 cognitive complexity <= 15
- Q2 python:S3358 nested conditional expression
- Q3 python:S107 parameters <= 13
- Q4 python:S1172 / S1481 / S1854 unused parameter / local / dead store (conservative)
"""

from __future__ import annotations

import ast
from collections import Counter
import io
import os
import tokenize
import re
import subprocess
from dataclasses import dataclass, field

MAX_COGNITIVE = 15
MAX_PARAMS = 13

FuncNode = ast.FunctionDef | ast.AsyncFunctionDef

# ---------------------------------------------------------------- scope

PROPERTIES_PATH = ".sonar/local.properties"


class ScopeError(Exception):
    """The Sonar scope file is missing or unusable (usage error, exit 2)."""


def _glob_re(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def read_properties(path: str) -> dict[str, str]:
    """Parse a Java-style `key=value` properties file (comments, `\\` line continuation)."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ScopeError(f"cannot read Sonar scope file {path}: {exc}") from exc
    props: dict[str, str] = {}
    pending = ""
    for raw in lines:
        line = pending + raw.strip()
        pending = ""
        if line.endswith("\\"):
            pending = line[:-1]
        elif line and line[0] not in "#!" and "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    return props


def _prop_list(props: dict[str, str], key: str) -> list[str]:
    return [item.strip().strip("/") or "." for item in props.get(key, "").split(",") if item.strip()]


@dataclass(frozen=True)
class Scope:
    """Which files Sonar analyzes as main code, read from the Sonar properties file."""

    sources: tuple[str, ...]
    test_dirs: tuple[str, ...]
    exclusions: tuple[re.Pattern[str], ...]
    test_inclusions: tuple[re.Pattern[str], ...]
    test_exclusions: tuple[re.Pattern[str], ...]

    def is_main(self, path: str) -> bool:
        """True when Sonar analyzes `path` as a main-code `.py` file.

        Baseline evidence (local:oig-proxy): Sonar raised Q1-Q4 rules only on main code.
        """
        if not path.endswith(".py"):
            return False
        if not any(_under(path, src) for src in self.sources):
            return False
        if any(r.match(path) for r in self.exclusions):
            return False
        return not (any(r.match(path) for r in self.test_inclusions)
                    or any(_under(path, d) for d in self.test_dirs))


def git_ignored(repo: str, paths: list[str]) -> set[str]:
    """Paths matched by a `.gitignore` rule, tracked or not.

    SonarQube drops files its SCM ignores, so a tracked file under an ignored directory is not
    analyzed as main code (baseline evidence: `scripts/` is ignored and none of it is in Sonar).
    """
    if not paths:
        return set()
    proc = subprocess.run(
        ["git", "-C", repo, "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(paths).encode("utf-8", errors="surrogateescape"), capture_output=True,
    )
    if proc.returncode not in (0, 1):
        raise GitError(f"git check-ignore: {proc.stderr.decode('utf-8', 'replace').strip() or proc.returncode}")
    return {p.decode("utf-8", errors="surrogateescape") for p in proc.stdout.split(b"\0") if p}


def _under(path: str, base: str) -> bool:
    return base == "." or path == base or path.startswith(base + "/")


def _patterns(props: dict[str, str], key: str) -> tuple[re.Pattern[str], ...]:
    return tuple(_glob_re(p) for p in _prop_list(props, key))


def load_scope(repo: str) -> Scope:
    """Read `.sonar/local.properties` of `repo`. Raises ScopeError when missing or unusable."""
    props = read_properties(os.path.join(repo, PROPERTIES_PATH))
    sources = _prop_list(props, "sonar.sources")
    if not sources:
        raise ScopeError(f"{PROPERTIES_PATH}: sonar.sources is not set")
    return Scope(
        sources=tuple(sources),
        test_dirs=tuple(_prop_list(props, "sonar.tests")),
        exclusions=_patterns(props, "sonar.exclusions"),
        test_inclusions=_patterns(props, "sonar.test.inclusions"),
        test_exclusions=_patterns(props, "sonar.test.exclusions"),
    )


# ---------------------------------------------------------------- Q1

_NESTING_STMT = (ast.If, ast.For, ast.AsyncFor, ast.While)
_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class _Cognitive:
    def __init__(self) -> None:
        self.total = 0
        self.scope: FuncNode | None = None

    def children(self, node: ast.AST, nesting: int) -> None:
        for child in ast.iter_child_nodes(node):
            self.visit(child, nesting)

    def body(self, stmts: list[ast.stmt], nesting: int) -> None:
        for stmt in stmts:
            self.visit(stmt, nesting)

    def visit_if(self, node: ast.If, nesting: int, elif_: bool) -> None:
        self.total += 1 if elif_ else 1 + nesting
        self.visit(node.test, nesting)
        self.body(node.body, nesting + 1)
        if (len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)
                and node.orelse[0].col_offset == node.col_offset):
            self.visit_if(node.orelse[0], nesting, True)
        elif node.orelse:
            self.total += 1
            self.body(node.orelse, nesting + 1)

    def visit_loop(self, node: ast.For | ast.AsyncFor | ast.While, nesting: int) -> None:
        self.total += 1 + nesting
        if isinstance(node, ast.While):
            self.visit(node.test, nesting)
        else:
            self.visit(node.target, nesting)
            self.visit(node.iter, nesting)
        self.body(node.body, nesting + 1)
        if node.orelse:
            self.total += 1
        self.body(node.orelse, nesting + 1)

    def visit_try(self, node: ast.Try, nesting: int) -> None:
        self.body(node.body, nesting)
        for handler in node.handlers:
            self.total += 1 + nesting
            if handler.type is not None:
                self.visit(handler.type, nesting)
            self.body(handler.body, nesting + 1)
        if node.orelse:
            self.total += 1
        self.body(node.orelse, nesting + 1)
        self.body(node.finalbody, nesting)

    def visit_boolop(self, node: ast.BoolOp, nesting: int) -> None:
        # one increment per BoolOp; a nested BoolOp only exists under parentheses or a different operator.
        # A bare `and` in the middle of an `or` chain splits it into two `or` sequences (+1 more).
        self.total += 1 + sum(1 for v in node.values if getattr(v, _SPLITS_SEQUENCE, False))
        for value in node.values:
            self.visit(value, nesting)

    def visit(self, node: ast.AST, nesting: int) -> None:
        if isinstance(node, ast.If):
            self.visit_if(node, nesting, False)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self.visit_loop(node, nesting)
        elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            self.visit_try(node, nesting)  # type: ignore[arg-type]
        elif isinstance(node, ast.IfExp):
            self.total += 1 + nesting
            self.visit(node.test, nesting)
            self.visit(node.body, nesting + 1)
            self.visit(node.orelse, nesting + 1)
        elif isinstance(node, ast.BoolOp):
            self.visit_boolop(node, nesting)
        elif isinstance(node, ast.Match):
            self.total += 1 + nesting
            self.visit(node.subject, nesting)
            for case in node.cases:
                if case.guard is not None:
                    self.visit(case.guard, nesting + 1)
                self.body(case.body, nesting + 1)
        elif isinstance(node, ast.ClassDef):
            self.class_body(node)
        elif isinstance(node, _FUNC_TYPES):
            self.nested_function(node, nesting)
        else:
            self.children(node, nesting)

    def class_body(self, node: ast.ClassDef) -> None:
        previous = self.scope
        self.scope = None
        self.body(node.body, 0)
        self.scope = previous

    def nested_function(self, node: ast.AST, nesting: int) -> None:
        if isinstance(node, ast.Lambda):
            self.visit(node.body, nesting)
            return
        for deco in getattr(node, "decorator_list", []):
            self.visit(deco, nesting)
        parent = self.scope
        wrapper = parent is not None and all(
            stmt is node or (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name))
            for stmt in parent.body
        )
        self.scope = node  # type: ignore[assignment]
        self.body(getattr(node, "body"), nesting if wrapper or parent is None else nesting + 1)
        self.scope = parent


_SPLITS_SEQUENCE = "splits_boolean_sequence"


def _operand_gap(lines: list[bytes], before: ast.expr, after: ast.expr) -> str:
    """Source text between two sibling operands with comments removed (AST columns are UTF-8 bytes)."""
    first, last = before.end_lineno or before.lineno, after.lineno
    end, start = before.end_col_offset or 0, after.col_offset
    if first == last:
        parts = [lines[first - 1][end:start]]
    else:
        parts = [lines[first - 1][end:], *lines[first:last - 1], lines[last - 1][:start]]
    return " ".join(re.sub(rb"#.*", b"", part).decode("utf-8", errors="replace") for part in parts)


def mark_split_sequences(tree: ast.AST, source: str) -> None:
    """Flag a bare `and` operand in the middle of an `or` chain (Sonar counts the `or` run twice).

    Sonar flattens unparenthesized and/or operators in token order and counts operator changes:
    `a or b and c or d` is or, and, or = 3, while the AST has one Or and one And = 2. A bare operand
    has no `(` between the previous operand and itself; the AST drops parentheses, so read the text.
    """
    lines = [line.encode("utf-8") for line in re.split(r"\r\n|\r|\n", source)]
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        for before, value in zip(node.values, node.values[1:-1]):
            if (isinstance(value, ast.BoolOp) and isinstance(value.op, ast.And)
                    and "(" not in _operand_gap(lines, before, value)):
                setattr(value, _SPLITS_SEQUENCE, True)


def cognitive_complexity(node: ast.AST) -> int:
    """Sonar python:S3776 cognitive complexity of a function (nested ones included).

    Run `mark_split_sequences` on the parsed module first (`analyze_module` does) for exact scores.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise TypeError("cognitive_complexity needs a FunctionDef or AsyncFunctionDef")
    calc = _Cognitive()
    calc.scope = node
    calc.body(node.body, 0)
    return calc.total


# ---------------------------------------------------------------- Q2 / Q3


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_COND_BOUNDARY = (
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Lambda, ast.JoinedStr,
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
)


def nested_conditionals(node: ast.AST) -> int:
    """Count conditional expressions that sit inside another one (S3358).

    Sonar looks for an enclosing conditional expression without crossing a comprehension,
    lambda, f-string or nested scope; every nested one is one finding, at any depth.
    """
    return _count_nested(node, False)


def _count_nested(node: ast.AST, inside: bool) -> int:
    total = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _COMPREHENSIONS):
            continue  # Sonar never reports a conditional inside a comprehension
        if isinstance(child, ast.IfExp):
            total += (1 if inside else 0) + _count_nested(child, True)
        else:
            total += _count_nested(child, inside and not isinstance(child, _COND_BOUNDARY))
    return total


def _conditional_anchor(statement: ast.stmt) -> str:
    if isinstance(statement, ast.Assign):
        anchor = tuple(ast.dump(t, include_attributes=False) for t in statement.targets)
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        anchor = (ast.dump(statement.target, include_attributes=False),)
    else:
        anchor = (type(statement).__name__,)
    return repr(anchor)


def _conditional_findings(func: FuncNode) -> Counter[str]:
    return _conditional_findings_in(func.body)


def _conditional_list_children(field_name: str, values: list, path: tuple[str, ...]):
    for index, child in enumerate(values):
        if isinstance(child, ast.AST):
            yield child, path + (f"{field_name}[{index}]",)


def _conditional_children(node: ast.AST, path: tuple[str, ...]):
    for field_name, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            yield value, path + (field_name,)
        elif isinstance(value, list):
            yield from _conditional_list_children(field_name, value, path)


def _visit_conditional_node(node: ast.AST, inside: bool, statement: ast.stmt | None,
                            path: tuple[str, ...], findings: Counter[str]) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Decorators and default values are evaluated in the enclosing scope.
        exprs = [*node.decorator_list, *node.args.defaults, *[d for d in node.args.kw_defaults if d]]
        for index, expr in enumerate(exprs):
            _visit_conditional_node(expr, False, node, (f"header[{index}]",), findings)
        return
    if isinstance(node, (ast.ClassDef, *_COMPREHENSIONS)):
        return
    if isinstance(node, ast.stmt):
        statement, path = node, ()
    if isinstance(node, ast.IfExp) and inside and statement is not None:
        findings[_conditional_anchor(statement) + ":" + repr(path)] += 1
    inside = isinstance(node, ast.IfExp) or (inside and not isinstance(node, _COND_BOUNDARY))
    for child, child_path in _conditional_children(node, path):
        _visit_conditional_node(child, inside, statement, child_path, findings)


def _conditional_findings_in(statements: list[ast.stmt]) -> Counter[str]:
    findings: Counter[str] = Counter()
    for stmt in statements:
        _visit_conditional_node(stmt, False, None, (), findings)
    return findings


def _assignments(func: FuncNode) -> list[ast.stmt]:
    assignments: list[ast.stmt] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            assignments.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in func.body:
        visit(stmt)
    return assignments


def _assignment_signature(node: ast.stmt) -> tuple[str, ...]:
    # Match the same expression site across target and literal edits.
    value = _shape_dump(node.value) if node.value is not None else "None"
    parts = [type(node).__name__, value]
    if isinstance(node, ast.AnnAssign):
        parts.append(_shape_dump(node.annotation))
    elif isinstance(node, ast.AugAssign):
        parts.append(_shape_dump(node.op))
    return tuple(parts)


def _shape_dump(node: ast.AST) -> str:
    """Describe AST structure while ignoring literal values."""
    if isinstance(node, ast.Constant):
        return "Constant"
    fields = ",".join(f"{name}={_shape_value(value)}" for name, value in ast.iter_fields(node))
    return f"{type(node).__name__}({fields})"


def _shape_value(value) -> str:
    if isinstance(value, ast.AST):
        return _shape_dump(value)
    if isinstance(value, list):
        return "[" + ",".join(_shape_value(item) for item in value) + "]"
    return repr(value)


def _introduced_assignments(assignments: list[ast.stmt], introduced: Counter[str]
                            ) -> dict[tuple[str, ...], list[ast.stmt]]:
    grouped: dict[tuple[str, ...], list[ast.stmt]] = {}
    for assignment in assignments:
        if introduced[_conditional_anchor(assignment)]:
            signature = _assignment_signature(assignment)
            grouped.setdefault(signature, []).append(assignment)
    return grouped


def _transfer_conditional_findings(old: ast.stmt, new: ast.stmt, added: Counter[str],
                                   remaining: Counter[str]) -> None:
    old_anchor = _conditional_anchor(old)
    new_anchor = _conditional_anchor(new)
    for old_key, count in _conditional_findings_in([old]).items():
        new_key = new_anchor + old_key[len(old_anchor):]
        matched = min(count, remaining[old_key], added[new_key])
        remaining[old_key] -= matched
        added[new_key] -= matched


def _match_assignment_findings(old: ast.stmt, candidates: dict[tuple[str, ...], list[ast.stmt]],
                               removed: Counter[str], introduced: Counter[str], added: Counter[str],
                               remaining: Counter[str]) -> None:
    old_anchor = _conditional_anchor(old)
    if not removed[old_anchor]:
        return
    matches = candidates.get(_assignment_signature(old), [])
    new = next((item for item in matches if introduced[_conditional_anchor(item)]), None)
    if new is None:
        return
    new_anchor = _conditional_anchor(new)
    matches.remove(new)
    removed[old_anchor] -= 1
    introduced[new_anchor] -= 1
    _transfer_conditional_findings(old, new, added, remaining)


def _match_renamed_assignments(unit: Unit, base_units: list[Unit], added: Counter[str],
                               remaining: Counter[str]) -> None:
    new_assignments = _assignments(unit.node)
    new_anchors = Counter(_conditional_anchor(n) for n in new_assignments)
    for base in base_units:
        old_assignments = _assignments(base.node)
        old_anchors = Counter(_conditional_anchor(n) for n in old_assignments)
        # Only pair disappearing targets with newly introduced targets, not moves between
        # existing assignments. Counts allow the same target to remain elsewhere.
        removed = old_anchors - new_anchors
        introduced = new_anchors - old_anchors
        new_by_signature = _introduced_assignments(new_assignments, introduced)
        for old in old_assignments:
            _match_assignment_findings(old, new_by_signature, removed, introduced, added, remaining)


def parameter_count(node: FuncNode, is_method: bool = False) -> int:
    """Parameters as Sonar python:S107 counts them (implicit self/cls of a method excluded)."""
    args = node.args
    total = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
    total += (args.vararg is not None) + (args.kwarg is not None)
    if is_method and not _is_static(node) and (args.posonlyargs or args.args):
        total -= 1
    return total


def _is_static(node: FuncNode) -> bool:
    for deco in node.decorator_list:
        name = deco.attr if isinstance(deco, ast.Attribute) else getattr(deco, "id", "")
        if name == "staticmethod":
            return True
    return False


# ---------------------------------------------------------------- Q4


@dataclass(frozen=True)
class Q4Finding:
    rule: str  # S1172 | S1481 | S1854
    name: str
    line: int


_DYNAMIC_SCOPE = {"locals", "vars", "eval", "exec"}


def _own_nodes(func: FuncNode):
    """Every node of `func` that belongs to its own scope (nested scopes excluded)."""
    stack = list(func.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            # decorators/defaults evaluate in this scope, the body does not
            if isinstance(node, ast.Lambda):
                stack.extend(node.args.defaults)
            else:
                stack.extend(getattr(node, "decorator_list", []))
                if not isinstance(node, ast.ClassDef):
                    stack.extend(node.args.defaults)
                    stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        stack.extend(ast.iter_child_nodes(node))


def _is_stub(func: FuncNode) -> bool:
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if isinstance(stmt, ast.Raise):
            continue
        return False
    return True


def _decorator_names(func: FuncNode) -> set[str]:
    names = set()
    for deco in func.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        names.add(target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", ""))
    return names


def _name_loads(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Name) and n.id == name and not isinstance(n.ctx, ast.Store)
        for n in ast.walk(node)
    )


def _statement_blocks(func: FuncNode) -> list[list[ast.stmt]]:
    blocks: list[list[ast.stmt]] = []
    for node in [func, *_own_nodes(func)]:
        for field_name in ("body", "orelse", "finalbody"):
            block = getattr(node, field_name, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                blocks.append(block)
        blocks.extend(h.body for h in getattr(node, "handlers", None) or [])
    return blocks


def _single_name_assign(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
        return stmt.targets[0].id
    return None


def _cannot_observe(value: ast.expr, name: str) -> bool:
    """True when evaluating `value` neither calls anything nor reads `name`."""
    for node in ast.walk(value):
        if isinstance(node, (ast.Call, ast.Await)):
            return False
        if isinstance(node, ast.Name) and node.id == name:
            return False
    return True


_GUARDED = (ast.Try, ast.With, ast.AsyncWith, *(
    (ast.TryStar,) if hasattr(ast, "TryStar") else ()))


def _guarded_statements(func: FuncNode) -> set[int]:
    """ids of statements that sit under a try/with, where an exception may be caught and the old value read."""
    guarded: set[int] = set()
    for node in [func, *_own_nodes(func)]:
        if isinstance(node, _GUARDED):
            for child in ast.iter_child_nodes(node):
                guarded.update(id(s) for s in ast.walk(child) if isinstance(s, ast.stmt))
    return guarded


def _cannot_raise(value: ast.expr) -> bool:
    """True when evaluating `value` cannot raise: literals and displays of literals only."""
    return all(
        isinstance(n, (ast.Constant, ast.Tuple, ast.List, ast.Set, ast.Dict, ast.expr_context))
        for n in ast.walk(value)
    )


def _dead_store_name(stmt: ast.stmt, later: ast.stmt, loaded: set[str], guarded: set[int]
                     ) -> str | None:
    name = _single_name_assign(stmt)
    if name is None or name.startswith("_") or name not in loaded:
        return None
    if _single_name_assign(later) != name:
        return None
    value = later.value  # type: ignore[attr-defined]
    if not _cannot_observe(value, name):
        return None
    if id(later) in guarded and not _cannot_raise(value):
        return None
    return name


def _dead_stores(func: FuncNode, loaded: set[str]) -> list[Q4Finding]:
    """S1854, adjacent only: `x = a` immediately rebound by `x = b` that cannot raise or read x.

    Outside a try/with the rebinding's exception leaves the function without reading x, so only
    a read by the rebinding itself matters. Under a try/with a raise may reach a handler that reads
    x, so the rebinding must be unable to raise.
    """
    findings: list[Q4Finding] = []
    guarded = _guarded_statements(func)
    for block in _statement_blocks(func):
        for stmt, later in zip(block, block[1:]):
            name = _dead_store_name(stmt, later, loaded, guarded)
            if name is not None:
                findings.append(Q4Finding("S1854", name, stmt.lineno))
    return findings


def _scan_names(func: FuncNode) -> tuple[set[str], dict[str, int], bool]:
    """(names read anywhere in func, store count per name, uses locals()/eval()-like calls)."""
    loaded: set[str] = set()
    stores: dict[str, int] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                stores[node.id] = stores.get(node.id, 0) + 1
            else:
                loaded.add(node.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            loaded.update(node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DYNAMIC_SCOPE:
            return loaded, stores, True
    return loaded, stores, False


def _unused_params(
    func: FuncNode, is_method: bool, loaded: set[str], mentions: dict[str, int],
) -> list[Q4Finding]:
    args = func.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if is_method and not _is_static(func) and (args.posonlyargs or args.args):
        first = (args.posonlyargs or args.args)[0]
        params = [p for p in params if p is not first]
    return [
        Q4Finding("S1172", p.arg, p.lineno)
        for p in params
        if p.arg not in loaded and not p.arg.startswith("_") and mentions.get(p.arg, 1) <= 1
    ]


def _assigned_name(node: ast.AST) -> ast.Name | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0]
    if isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
        return node.target
    return None


def _unused_locals(func: FuncNode, loaded: set[str], stores: dict[str, int]) -> list[Q4Finding]:
    """S1481: local bound exactly once by plain `name = value` and never read."""
    param_names = {a.arg for a in ast.walk(func.args) if isinstance(a, ast.arg)}
    findings = []
    for node in _own_nodes(func):
        target = _assigned_name(node)
        if target is None or target.id.startswith("_") or target.id in param_names:
            continue
        if target.id not in loaded and stores.get(target.id) == 1:
            findings.append(Q4Finding("S1481", target.id, node.lineno))
    return findings


def unused_names(
    func: FuncNode,
    is_method: bool = False,
    class_has_bases: bool = False,
    referenced: frozenset[str] | set[str] = frozenset(),
    name_mentions: dict[str, int] | None = None,
) -> list[Q4Finding]:
    """Conservative S1172 / S1481 / S1854: report only what Sonar certainly reports.

    `referenced`: function names used as a value in the module (callbacks keep their signature).
    `name_mentions`: occurrences of each identifier in the function's source text.
    """
    loaded, stores, dynamic = _scan_names(func)
    if dynamic:
        return []
    # Sonar skips stubs, decorated functions, dunders, callbacks and possible overrides
    skip_params = (
        _is_stub(func) or func.decorator_list or func.name.startswith("__")
        or func.name in referenced or (is_method and class_has_bases)
    )
    findings = [] if skip_params else _unused_params(func, is_method, loaded, name_mentions or {})
    findings.extend(_unused_locals(func, loaded, stores))
    findings.extend(_dead_stores(func, loaded))
    return findings


# ---------------------------------------------------------------- module analysis


@dataclass
class Unit:
    qualname: str
    node: FuncNode
    outermost: bool
    first_line: int  # first decorator or def line
    end_line: int
    header_end: int = 0
    body_same_line: bool = False
    declaration_lines: set[int] = field(default_factory=set)
    metrics: dict[str, int] = field(default_factory=dict)
    q4: dict[tuple[str, str], int] = field(default_factory=dict)
    q2: Counter[str] = field(default_factory=Counter)


def _referenced_function_names(tree: ast.AST) -> set[str]:
    """Names (and attribute names) read as a value rather than called: callbacks."""
    called: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called.add(id(node.func))
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and id(node) not in called:
            refs.add(node.id)
        elif isinstance(node, ast.Attribute) and id(node) not in called:
            refs.add(node.attr)
    return refs


def _mentions(source_lines: list[str], func: FuncNode) -> dict[str, int]:
    text = "\n".join(source_lines[func.lineno - 1 : func.end_lineno])
    counts: dict[str, int] = {}
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        counts[word] = counts.get(word, 0) + 1
    return counts


def _without_comments(source: str) -> list[str]:
    """Source lines with comment text blanked, so editing a comment never changes a name count."""
    lines = source.splitlines()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                row, col = token.start
                lines[row - 1] = lines[row - 1][:col]
    except (tokenize.TokenError, IndentationError):
        pass
    return lines


def _function_headers(source: str) -> dict[int, int]:
    headers: dict[int, int] = {}
    tokens = iter(tokenize.generate_tokens(io.StringIO(source).readline))
    for token in tokens:
        if token.type != tokenize.NAME or token.string != "def":
            continue
        start = token.start[0]
        depth = 0
        for part in tokens:
            if part.string in ("(", "[", "{"):
                depth += 1
            elif part.string in (")", "]", "}"):
                depth -= 1
            elif part.string == ":" and depth == 0:
                headers[start] = part.end[0]
                break
    return headers


def _selected(unit: Unit, line: int) -> bool:
    """A changed line selects the function it lies in: decorators, header (signature), body, and
    nested headers. A deletion is recorded at the head line before it (see `_hunk_lines`)."""
    return unit.first_line <= line <= unit.end_line


def _body_line(unit: Unit, line: int) -> bool:
    return (unit.header_end <= line <= unit.end_line
            and (line > unit.header_end or unit.body_same_line)
            and line not in unit.declaration_lines)


class _Walker:
    def __init__(self, source: str) -> None:
        self.tree = ast.parse(source)
        mark_split_sequences(self.tree, source)
        self.lines = _without_comments(source)
        self.headers = _function_headers(source)
        self.referenced = _referenced_function_names(self.tree)
        self.units: list[Unit] = []

    def run(self) -> list[Unit]:
        self.walk(self.tree, "", False, (False, False))
        return self.units

    def add_function(self, node: FuncNode, prefix: str, in_function: bool, class_info: tuple[bool, bool]) -> str:
        qual = f"{prefix}{node.name}"
        first = min([node.lineno, *[d.lineno for d in node.decorator_list]])
        unit = Unit(qual, node, not in_function, first, node.end_lineno or node.lineno)
        unit.header_end = self.headers[node.lineno]
        unit.body_same_line = node.body[0].lineno == unit.header_end
        for child in ast.walk(node):
            if child is node or not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = min([child.lineno, *[d.lineno for d in child.decorator_list]])
            end = self.headers[child.lineno]
            if child.body[0].lineno == end:
                end -= 1
            unit.declaration_lines.update(range(start, end + 1))
        is_method, has_bases = class_info
        if unit.outermost:
            unit.metrics["Q1"] = cognitive_complexity(node)
        unit.q2 = _conditional_findings(node)
        unit.metrics["Q3"] = parameter_count(node, is_method)
        if not in_function:  # Sonar reports unused names on top-level functions and methods only
            mentions = _mentions(self.lines, node)
            for f in unused_names(node, is_method, has_bases, self.referenced, mentions):
                key = (f.rule, f.name)
                unit.q4[key] = unit.q4.get(key, 0) + 1
        self.units.append(unit)
        return qual

    def walk(self, parent: ast.AST, prefix: str, in_function: bool, class_info: tuple[bool, bool]) -> None:
        # class_info = (directly inside a class, that class has bases)
        for node in ast.iter_child_nodes(parent):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = self.add_function(node, prefix, in_function, class_info)
                self.walk(node, f"{qual}.<locals>.", True, (False, False))
            elif isinstance(node, ast.ClassDef):
                self.walk(node, f"{prefix}{node.name}.", in_function, (True, bool(node.bases)))
            else:
                # A statement such as `if`/`try` directly in a class body still binds its defs as
                # methods, so class context passes through it; a function body resets it.
                self.walk(node, prefix, in_function, class_info)


def analyze_module(source: str) -> list[Unit]:
    """Score every function of a module. Raises SyntaxError on unparsable source."""
    if source.startswith("\ufeff"):
        source = source[1:]
    return _Walker(source).run()


# ---------------------------------------------------------------- git diff + ratchet


class GitError(Exception):
    pass


def git(repo: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or 'exit ' + str(proc.returncode)}")
    return proc.stdout


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _hunk_lines(header: str) -> set[int]:
    m = _HUNK.match(header)
    if not m:
        return set()
    start = int(m.group(1))
    count = 1 if m.group(2) is None else int(m.group(2))
    if count == 0:  # deletion-only hunk: `start` is the head line before the removed text
        return {start} if start > 0 else set()
    return set(range(start, start + count))


_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _octal_git_escape(body: str, index: int) -> tuple[bytes, int]:
    end = index + 1
    while end < min(index + 4, len(body)) and body[end] in "01234567":
        end += 1
    return bytes((int(body[index + 1:end], 8) & 0xFF,)), end - index


def _decode_git_escape(body: str, index: int) -> tuple[bytes, int]:
    if index + 1 == len(body):
        return b"\\", 1
    char = body[index + 1]
    if char in "01234567":
        return _octal_git_escape(body, index)
    if char in _C_ESCAPES:
        return bytes((_C_ESCAPES[char],)), 2
    return body[index:index + 2].encode("utf-8", errors="surrogateescape"), 2


def _unquote_git_path(raw: str) -> str:
    """Decode one path as Git writes it in diff headers.

    Git C-quotes a name that contains control characters, `"` or `\\`, and (with the default
    core.quotePath) every non-ASCII byte as an octal escape: `"b/caf\\303\\251.py"` is the UTF-8
    name `b/café.py`. Escapes are bytes, so they are decoded as UTF-8 before text conversion.
    """
    if len(raw) < 2 or not (raw.startswith('"') and raw.endswith('"')):
        return raw
    body = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        if body[i] != "\\":
            out.extend(body[i].encode("utf-8", errors="surrogateescape"))
            i += 1
        else:
            escaped, advance = _decode_git_escape(body, i)
            out.extend(escaped)
            i += advance
    return out.decode("utf-8", errors="surrogateescape")


def _header_path(header: str) -> str | None:
    """Repository path of a `+++ ` header line, or None (deleted file or no b/ prefix)."""
    raw = header[4:]
    if raw.endswith("\t"):
        raw = raw[:-1]
    if raw.startswith('"'):
        path = _unquote_git_path(raw)
    else:
        # A TAB in a name is quoted, so a trailing TAB is Git's header delimiter.
        path = raw
    return path[2:] if path.startswith("b/") else None


def _rename_path(line: str, prefix: str) -> str:
    return _unquote_git_path(line[len(prefix):].rstrip("\t"))


def changed_lines(repo: str, merge_base: str, head: str,
                  renames: dict[str, str] | None = None) -> dict[str, set[int]]:
    """Head-side line numbers added, modified or (as the preceding line) deleted per path.

    `renames`, when given, is filled with head path -> base path for renamed files."""
    out = git(repo, "diff", "-U0", "-M", "--no-color", "--diff-filter=AMR",
              merge_base, head, "--", "*.py")
    result: dict[str, set[int]] = {}
    path = None
    rename_from = None
    pending: set[int] | None = None
    for line in out.splitlines():
        if line.startswith("diff --git "):
            path = rename_from = None
        elif line.startswith("rename from "):
            rename_from = _rename_path(line, "rename from ")
        elif line.startswith("rename to "):
            path = _rename_path(line, "rename to ")
            result.setdefault(path, set())
            if renames is not None and rename_from is not None:
                renames[path] = rename_from
        elif line.startswith("+++ "):
            path = _header_path(line)
            if path is not None:
                result.setdefault(path, set())
        elif line.startswith("--- "):
            continue
        elif path is not None and line.startswith("@@"):
            lines = _hunk_lines(line)
            pending = lines if re.search(r"\+\d+,0 @@", line) else None
            if pending is None:
                result[path] |= lines
        elif path is not None and pending is not None and line.startswith("-"):
            # A deleted top-level (unindented) line sits outside any function, even right after one.
            if line[1:2] in (" ", "\t"):
                result[path] |= pending
            pending = None
    return result


def _show(repo: str, rev: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", repo, "show", f"{rev}:{path}"], capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


@dataclass
class Finding:
    path: str
    line: int
    qualname: str
    rule: str
    old: str
    new: str

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.qualname} {self.rule} {self.old}->{self.new}"


LIMITS = {"Q1": ("S3776", MAX_COGNITIVE), "Q3": ("S107", MAX_PARAMS)}


def _base_index(units: list[Unit]) -> dict[str, list[Unit]]:
    index: dict[str, list[Unit]] = {}
    for unit in units:
        index.setdefault(unit.qualname, []).append(unit)
    return index


def _base_max(base_units: list[Unit] | None, getter) -> int:
    return 0 if base_units is None else max((getter(b) for b in base_units), default=0)


def _limit_findings(path: str, unit: Unit, base_units: list[Unit] | None) -> list[Finding]:
    findings = []
    for key, (rule, limit) in LIMITS.items():
        if key not in unit.metrics:
            continue
        new = unit.metrics[key]
        old = _base_max(base_units, lambda b, k=key: b.metrics.get(k, 0))
        # NEW: pass the threshold. MODIFIED: never increase, even below it.
        if (base_units is None and new > limit) or (base_units is not None and new > old):
            findings.append(Finding(path, unit.node.lineno, unit.qualname, rule, str(old), str(new)))
    return findings


def _q4_counts(unit: Unit, base_units: list[Unit] | None) -> list[tuple[str, int, int]]:
    """(rule, head count, base count). Unused parameters compare by name; unused locals and dead
    stores by rule total, so renaming a pre-existing unused local is not a new finding."""
    totals: dict[str, list[int]] = {}
    counted = []
    for (rule, name), new in sorted(unit.q4.items()):
        if rule == "S1172":
            counted.append((rule, new, _base_max(base_units, lambda b, k=(rule, name): b.q4.get(k, 0))))
        else:
            totals.setdefault(rule, [0, 0])[0] += new
    for rule in {r for r in totals}:
        totals[rule][1] = _base_max(
            base_units, lambda b, r=rule: sum(v for (rr, _), v in b.q4.items() if rr == r))
    counted.extend((rule, new, old) for rule, (new, old) in sorted(totals.items()))
    return counted


def check_unit(path: str, unit: Unit, base_units: list[Unit] | None) -> list[Finding]:
    """Ratchet one changed function. `base_units` is None for a NEW function."""
    findings = _limit_findings(path, unit, base_units)
    old_q2 = Counter() if base_units is None else sum((b.q2 for b in base_units), Counter())
    added_q2 = unit.q2 - old_q2
    if added_q2 and base_units:
        _match_renamed_assignments(unit, base_units, added_q2, old_q2 - unit.q2)
        added_q2 += Counter()  # Drop zero counts after matching.
    counted = [("S3358", sum(unit.q2.values()), sum(old_q2.values()))] if added_q2 else []
    counted.extend(_q4_counts(unit, base_units))
    findings.extend(
        Finding(path, unit.node.lineno, unit.qualname, rule, str(old), str(new))
        for rule, new, old in counted if rule == "S3358" or new > old
    )
    return findings


def _analyze_or_none(source: str | None, path: str, warn) -> list[Unit] | None:
    if source is None:
        return None
    try:
        return analyze_module(source)
    except (SyntaxError, ValueError) as exc:
        if warn:
            warn(f"{path}: not analyzed ({exc})")
        return None


def _check_file(repo: str, merge_base: str, head: str, path: str, lines: set[int], warn,
                base_path: str | None = None) -> list[Finding]:
    head_units = _analyze_or_none(_show(repo, head, path), path, warn)
    if not head_units:
        return []
    touched = [u for u in head_units if any(_selected(u, n) for n in lines)]
    if not touched:
        return []
    base_units = _analyze_or_none(_show(repo, merge_base, base_path or path), path, None)
    base_index = _base_index(base_units or [])
    findings: list[Finding] = []
    for unit in touched:
        findings.extend(check_unit(path, unit, base_index.get(unit.qualname)))
    return findings


def check_diff(repo: str, base: str, head: str = "HEAD", warn=None) -> list[Finding]:
    merge_base = git(repo, "merge-base", base, head).strip()
    if not merge_base:
        raise GitError(f"no merge-base between {base} and {head}")
    findings: list[Finding] = []
    scope = load_scope(repo)
    renames: dict[str, str] = {}
    candidates = {
        path: lines for path, lines in changed_lines(repo, merge_base, head, renames).items()
        if scope.is_main(path) and lines
    }
    ignored = git_ignored(repo, sorted(candidates))
    for path, lines in sorted(candidates.items()):
        if path not in ignored:
            findings.extend(_check_file(repo, merge_base, head, path, lines, warn, renames.get(path)))
    return findings
