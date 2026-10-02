"""Pytest helpers for the destruction test-suite.

The test classes are plain classes (they do not inherit from
``unittest.TestCase``) but use unittest-style ``assert*`` methods. Attach
self-contained equivalents at collection time so the assertions behave as
intended without modifying the test files.
"""

from contextlib import contextmanager


def _assert_is_not_none(self, obj, msg=None):
    if obj is None:
        raise AssertionError(msg or "Expected a non-None result, got None")


def _assert_is_none(self, obj, msg=None):
    if obj is not None:
        raise AssertionError(msg or "Expected None, got %r" % (obj,))


def _assert_true(self, expr, msg=None):
    if not expr:
        raise AssertionError(msg or "%r is not true" % (expr,))


def _assert_false(self, expr, msg=None):
    if expr:
        raise AssertionError(msg or "%r is not false" % (expr,))


def _assert_equal(self, first, second, msg=None):
    if first != second:
        raise AssertionError(msg or "%r != %r" % (first, second))


def _assert_in(self, member, container, msg=None):
    if member not in container:
        raise AssertionError(msg or "%r not found in %r" % (member, container))


def _assert_greater(self, first, second, msg=None):
    if not first > second:
        raise AssertionError(msg or "%r not greater than %r" % (first, second))


def _assert_less(self, first, second, msg=None):
    if not first < second:
        raise AssertionError(msg or "%r not less than %r" % (first, second))


def _assert_is_instance(self, obj, cls, msg=None):
    if not isinstance(obj, cls):
        raise AssertionError(msg or "%r is not an instance of %r" % (obj, cls))


@contextmanager
def _assert_raises(self, expected):
    try:
        yield
    except expected:
        return
    raise AssertionError("%r was not raised" % (expected,))


_ASSERTIONS = {
    "assertIsNotNone": _assert_is_not_none,
    "assertIsNone": _assert_is_none,
    "assertTrue": _assert_true,
    "assertFalse": _assert_false,
    "assertEqual": _assert_equal,
    "assertIn": _assert_in,
    "assertGreater": _assert_greater,
    "assertLess": _assert_less,
    "assertIsInstance": _assert_is_instance,
    "assertRaises": _assert_raises,
}


def pytest_collection_modifyitems(items):
    for item in items:
        cls = getattr(item, "cls", None)
        if cls is None:
            continue
        for name, func in _ASSERTIONS.items():
            if not hasattr(cls, name):
                setattr(cls, name, func)
