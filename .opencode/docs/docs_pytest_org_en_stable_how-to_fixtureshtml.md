# How to use fixtures - pytest documentation

> Source: https://docs.pytest.org/en/stable/how-to/fixtures.html
> Cached: 2026-02-17T19:30:30.330Z

---

# How to use fixtures[¶](#how-to-use-fixtures)

See also

[About fixtures](../explanation/fixtures.html#about-fixtures)

See also

[Fixtures reference](../reference/fixtures.html#reference-fixtures)

## “Requesting” fixtures[¶](#requesting-fixtures)

At a basic level, test functions request fixtures they require by declaring
them as arguments.
When pytest goes to run a test, it looks at the parameters in that test
function’s signature, and then searches for fixtures that have the same names as
those parameters. Once pytest finds them, it runs those fixtures, captures what
they returned (if anything), and passes those objects into the test function as
arguments.

### Quick example[¶](#quick-example)

import pytest

class Fruit:
    def __init__(self, name):
        self.name = name
        self.cubed = False

    def cube(self):
        self.cubed = True

class FruitSalad:
    def __init__(self, *fruit_bowl):
        self.fruit = fruit_bowl
        self._cube_fruit()

    def _cube_fruit(self):
        for fruit in self.fruit:
            fruit.cube()

# Arrange
@pytest.fixture
def fruit_bowl():
    return [Fruit("apple"), Fruit("banana")]

def test_fruit_salad(fruit_bowl):
    # Act
    fruit_salad = FruitSalad(*fruit_bowl)

    # Assert
    assert all(fruit.cubed for fruit in fruit_salad.fruit)

In this example, `test_fruit_salad` “**requests**” `fruit_bowl` (i.e.
`def test_fruit_salad(fruit_bowl):`), and when pytest sees this, it will
execute the `fruit_bowl` fixture function and pass the object it returns into
`test_fruit_salad` as the `fruit_bowl` argument.
Here’s roughly
what’s happening if we were to do it by hand:
def fruit_bowl():
    return [Fruit("apple"), Fruit("banana")]

def test_fruit_salad(fruit_bowl):
    # Act
    fruit_salad = FruitSalad(*fruit_bowl)

    # Assert
    assert all(fruit.cubed for fruit in fruit_salad.fruit)

# Arrange
bowl = fruit_bowl()
test_fruit_salad(fruit_bowl=bowl)

### Fixtures can **request** other fixtures[¶](#fixtures-can-request-other-fixtures)

One of pytest’s greatest strengths is its extremely flexible fixture system. It
allows us to boil down complex requirements for tests into more simple and
organized functions, where we only need to have each one describe the things
they are dependent on. We’ll get more into this further down, but for now,
here’s a quick example to demonstrate how fixtures can use other fixtures:
# contents of test_append.py
import pytest

# Arrange
@pytest.fixture
def first_entry():
    return "a"

# Arrange
@pytest.fixture
def order(first_entry):
    return [first_entry]

def test_string(order):
    # Act
    order.append("b")

    # Assert
    assert order == ["a", "b"]

Notice that this is the same example from above, but very little changed. The
fixtures in pytest **request** fixtures just like tests. All the same
**requesting** rules apply to fixtures that do for tests. Here’s how this
example would work if we did it by hand:
def first_entry():
    return "a"

def order(first_entry):
    return [first_entry]

def test_string(order):
    # Act
    order.append("b")

    # Assert
    assert order == ["a", "b"]

entry = first_entry()
the_list = order(first_entry=entry)
test_string(order=the_list)

### Fixtures are reusable[¶](#fixtures-are-reusable)

One of the things that makes pytest’s fixture system so powerful, is that it
gives us the ability to define a generic setup step that can be reused over and
over, just like a normal function would be used. Two different tests can request
the same fixture and have pytest give each test their own result from that
fixture.
This is extremely useful for making sure tests aren’t affected by each other. We
can use this system to make sure each test gets its own fresh batch of data and
is starting from a clean state so it can provide consistent, repeatable results.
Here’s an example of how this can come in handy:

# contents of test_append.py
import pytest

# Arrange
@pytest.fixture
def first_entry():
    return "a"

# Arrange
@pytest.fixture
def order(first_entry):
    return [first_entry]

def test_string(order):
    # Act
    order.append("b")

    # Assert
    assert order == ["a", "b"]

def test_int(order):
    # Act
    order.append(2)

    # Assert
    assert order == ["a", 2]

Each test here is being given its own copy of that `list` object,
which means the `order` fixture is getting executed twice (the same
is true for the `first_entry` fixture). If we were to do this by hand as
well, it would look something like this:
def first_entry():
    return "a"

def order(first_entry):
    return [first_entry]

def test_string(order):
    # Act
    order.append("b")

    # Assert
    assert order == ["a", "b"]

def test_int(order):
    # Act
    order.append(2)

    # Assert
    assert order == ["a", 2]

entry = first_entry()
the_list = order(first_entry=entry)
test_string(order=the_list)

entry = first_entry()
the_list = order(first_entry=entry)
test_int(order=the_list)

### A test/fixture can **request** more than one fixture at a time[¶](#a-test-fixture-can-request-more-than-one-fixture-at-a-time)

Tests and fixtures aren’t limited to **requesting** a single fixture at a time.
They can request as many as they like. Here’s another quick example to
demonstrate:
# contents of test_append.py
import pytest

# Arrange
@pytest.fixture
def first_entry():
    return "a"

# Arrange
@pytest.fixture
def second_entry():
    return 2

# Arrange
@pytest.fixture
def order(first_entry, second_entry):
    return [first_entry, second_entry]

# Arrange
@pytest.fixture
def expected_list():
    return ["a", 2, 3.0]

def test_string(order, expected_list):
    # Act
    order.append(3.0)

    # Assert
    assert order == expected_list

### Fixtures can be **requested** more than once per test (return values are cached)[¶](#fixtures-can-be-requested-more-than-once-per-test-return-values-are-cached)

Fixtures can also be **requested** more than once during the same test, and
pytest won’t execute them again for that test. This means we can **request**
fixtures in multiple fixtures that are dependent on them (and even again in the
test itself) without those fixtures being executed more than once.
# contents of test_append.py
import pytest

# Arrange
@pytest.fixture
def first_entry():
    return "a"

# Arrange
@pytest.fixture
def order():
    return []

# Act
@pytest.fixture
def append_first(order, first_entry):
    return order.append(first_entry)

def test_string_only(append_first, order, first_entry):
    # Assert
    assert order == [first_entry]

If a **requested** fixture was executed once for every time it was **requested**
during a test, then this test would fail because both `append_first` and
`test_string_only` would see `order` as an empty list (i.e. `[]`), but
since the return value of `order` was cached (along with any side effects
executing it may have had) after the first time it was called, both the test and
`append_first` were referencing the same object, and the test saw the effect
`append_first` had on that object.

## Autouse fixtures (fixtures you don’t have to request)[¶](#autouse-fixtures-fixtures-you-don-t-have-to-request)

Sometimes you may want to have a fixture (or even several) that you know all
your tests will depend on. “Autouse” fixtures are a convenient way to make all
tests automatically **request** them. This can cut out a
lot of redundant **requests**, and can even provide more advanced fixture usage
(more on that further down).
We can make a fixture an autouse fixture by passing in `autouse=True` to the
fixture’s decorator. Here’s a simple example for how they can be used:
# contents of test_append.py
import pytest

@pytest.fixture
def first_entry():
    return "a"

@pytest.fixture
def order(first_entry):
    return []

@pytest.fixture(autouse=True)
def append_first(order, first_entry):
    return order.append(first_entry)

def test_string_only(order, first_entry):
    assert order == [first_entry]

def test_string_and_int(order, first_entry):
    order.append(2)
    assert order == [first_entry, 2]

In this example, the `append_first` fixture is an autouse fixture. Because it
happens automatically, both tests are affected by it, even though neither test
**requested** it. That doesn’t mean they *can’t* be **requested** though; just
that it isn’t *necessary*.

## Scope: sharing fixtures across classes, modules, packages or session[¶](#scope-sharing-fixtures-across-classes-modules-packages-or-session)

Fixtures requiring network access depend on connectivity and are
usually time-expensive to create.  Extending the previous example, we
can add a `scope="module"` parameter to the
[`&#64;pytest.fixture`](../reference/reference.html#pytest.fixture) invocation
to cause a `smtp_connection` fixture function, responsible to create a connection to a preexisting SMTP server, to only be invoked
once per test *module* (the default is to invoke once per test *function*).
Multiple test functions in a test module will thus
each receive the same `smtp_connection` fixture instance, thus saving time.
Possible values for `scope` are: `function`, `class`, `module`, `package` or `session`.
The next example puts the fixture function into a separate `conftest.py` file
so that tests from multiple test modules in the directory can
access the fixture function:
# content of conftest.py
import smtplib

import pytest

@pytest.fixture(scope="module")
def smtp_connection():
    return smtplib.SMTP("smtp.gmail.com", 587, timeout=5)

# content of test_module.py

def test_ehlo(smtp_connection):
    response, msg = smtp_connection.ehlo()
    assert response == 250
    assert b"smtp.gmail.com" in msg
    assert 0  # for demo purposes

def test_noop(smtp_connection):
    response, msg = smtp_connection.noop()
    assert response == 250
    assert 0  # for demo purposes

Here, the `test_ehlo` needs the `smtp_connection` fixture value.  pytest
will discover and call the [`&#64;pytest.fixture`](../reference/reference.html#pytest.fixture)
marked `smtp_connection` fixture function.  Running the test looks like this:
$ pytest test_module.py
=========================== test session starts ============================
platform linux -- Python 3.x.y, pytest-9.x.y, pluggy-1.x.y
rootdir: /home/sweet/project
collected 2 items

test_module.py FF                                                    [100%]

================================= FAILURES =================================
________________________________ test_ehlo _________________________________

smtp_connection = <smtplib.SMTP object at 0xdeadbeef0001>

    def test_ehlo(smtp_connection):
        response, msg = smtp_connection.ehlo()
        assert response == 250
        assert b"smtp.gmail.com" in msg
>       assert 0  # for demo purposes
        ^^^^^^^^
E       assert 0

test_module.py:7: AssertionError
________________________________ test_noop _________________________________

smtp_connection = <smtplib.SMTP object at 0xdeadbeef0001>

    def test_noop(smtp_connection):
        response, msg = smtp_connection.noop()
        assert response == 250
>       assert 0  # for demo purposes
        ^^^^^^^^
E       assert 0

test_module.py:13: AssertionError
========================= short test summary info ==========================
FAILED test_module.py::test_ehlo - assert 0
FAILED test_module.py::test_noop - assert 0
============================ 2 failed in 0.12s =============================

You see the two `assert 0` failing and more importantly you can also see
that the **exactly same** `smtp_connection` object was passed into the
two test functions because pytest shows the incoming argument values in the
traceback.  As a result, the two test functions using `smtp_connection` run
as quick as a single one because they reuse the same instance.
If you decide that you rather want to have a session-scoped `smtp_connection`
instance, you can simply declare it:
@pytest.fixture(scope="session")
def smtp_connection():
    # the returned fixture value will be shared for
    # all tests requesting it
    ...

### Fixture scopes[¶](#fixture-scopes)

Fixtures are created when first requested by a test, and are destroyed based on their `scope`:

`function`: the default scope, the fixture is destroyed at the end of the test.

`class`: the fixture is destroyed during teardown of the last test in the class.

`module`: the fixture is destroyed during teardown of the last test in the module.

`package`: the fixture is destroyed during teardown of the last test in the package where the fixture is defined, including sub-packages and sub-directories within it.

`session`: the fixture is destroyed at the end of the test session.

Note

Pytest only caches one instance of a fixture at a time, which
means that when using a parametrized fixture, pytest may invoke a fixture more than once in
the given scope.

### Dynamic scope[¶](#dynamic-scope)

Added in version 5.2.

In some cases, you might want to change the scope of the fixture without changing the code.
To do that, pass a callable to `scope`. The callable must return a string with a valid scope
and will be executed only once - during the fixture definition. It will be called with two
keyword arguments - `fixture_name` as a string and `config` with a configuration object.
This can be especially useful when dealing with fixtures that need time for setup, like spawning
a docker container. You can use the command-line argument to control the scope of the spawned
containers for different environments. See the example below.
def determine_scope(fixture_name, config):
    if config.getoption("--keep-containers", None):
        return "session"
    return "function"

@pytest.fixture(scope=determine_scope)
def docker_container():
    yield spawn_container()

## Teardown/Cleanup (AKA Fixture finalization)[¶](#teardown-cleanup-aka-fixture-finalization)

When we run our tests, we’ll want to make sure they clean up after themselves so
they don’t mess with any other tests (and also so that we don’t leave behind a
mountain of test data to bloat the system). Fixtures in pytest offer a very
useful teardown system, which allows us to define the specific steps necessary
for each fixture to clean up after itself.
This system can be leveraged in two ways.

### 1. `yield` fixtures (recommended)[¶](#yield-fixtures-recommended)

“Yield” fixtures `yield` instead of `return`. With these
fixtures, we can run some code and pass an object back to the requesting
fixture/test, just like with the other fixtures. The only differences are:

`return` is swapped out for `yield`.

Any teardown code for that fixture is placed *after* the `yield`.

Once pytest figures out a linear order for the fixtures, it will run each one up
until it returns or yields, and then move on to the next fixture in the list to
do the same thing.
Once the test is finished, pytest will go back down the list of fixtures, but in
the *reverse order*, taking each one that yielded, and running the code inside
it t

... [Content truncated]