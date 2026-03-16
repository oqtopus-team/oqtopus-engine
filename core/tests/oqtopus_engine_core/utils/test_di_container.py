import pytest

from oqtopus_engine_core.utils.di_container import DiContainer


# ----------------------------------------------------------------------
# Test helper classes (local test doubles)
# ----------------------------------------------------------------------
class DummyA:
    """A simple class for singleton/prototype tests."""
    def __init__(self, x: int, label: str):
        self.x = x
        self.label = label


class DummyB:
    """Another class for constructor mismatch testing."""
    def __init__(self, value: int):
        self.value = value


# Fully-qualified path strings for the test classes above.
# These must match the actual module path where this test file lives.
# If your tests are located under tests/, adjust accordingly.
DUMMYA_PATH = f"{__name__}.DummyA"
DUMMYB_PATH = f"{__name__}.DummyB"


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_singleton_scope_default():
    """
    Default scope should be singleton when _scope_ is omitted.
    get() must return the same instance.
    """
    config = {
        "obj": {
            "_target_": DUMMYA_PATH,
            "x": 123,
            "label": "abc",
        }
    }
    dicon = DiContainer(config)

    a1 = dicon.get("obj")
    a2 = dicon.get("obj")

    assert a1 is a2
    assert a1.x == 123
    assert a1.label == "abc"


def test_explicit_singleton_scope():
    """
    _scope_: singleton should behave identically to omitted scope.
    """
    config = {
        "obj": {
            "_target_": DUMMYA_PATH,
            "_scope_": "singleton",
            "x": 5,
            "label": "hello",
        }
    }
    dicon = DiContainer(config)

    a1 = dicon.get("obj")
    a2 = dicon.get("obj")

    assert a1 is a2
    assert a1.label == "hello"


def test_prototype_scope():
    """
    _scope_: prototype should produce a new instance every time.
    """
    config = {
        "obj": {
            "_target_": DUMMYA_PATH,
            "_scope_": "prototype",
            "x": 1,
            "label": "x",
        }
    }
    dicon = DiContainer(config)

    a1 = dicon.get("obj")
    a2 = dicon.get("obj")

    assert a1 is not a2
    assert a1.x == 1
    assert a2.x == 1


def test_unknown_dependency_key():
    """
    get() must raise KeyError for missing component names.
    """
    dicon = DiContainer({})
    with pytest.raises(KeyError):
        dicon.get("notdefined")


def test_missing_target_error():
    """
    Missing _target_ should raise ValueError.
    """
    config = {
        "obj": {
            "x": 1,
            "label": "no_target",
        }
    }
    dicon = DiContainer(config)

    with pytest.raises(ValueError):
        dicon.get("obj")


def test_invalid_target_path():
    """
    An invalid _target_ string should raise ImportError.
    """
    config = {
        "obj": {
            "_target_": "no.such.module.ClassName",
            "x": 10,
            "label": "z",
        }
    }
    dicon = DiContainer(config)

    with pytest.raises(ImportError):
        dicon.get("obj")


def test_constructor_argument_mismatch():
    """
    If constructor arguments do not match, TypeError should be raised.
    """
    config = {
        "obj": {
            "_target_": DUMMYB_PATH,  # DummyB expects only 'value'
            "wrong": 999,             # wrong kwarg
        }
    }
    dicon = DiContainer(config)

    with pytest.raises(TypeError):
        dicon.get("obj")


def test_underbar_keys_are_excluded_from_kwargs():
    """
    Keys starting with '_' are excluded from constructor kwargs.
    Only non-underscore keys must be passed to the constructor.
    """
    config = {
        "obj": {
            "_target_": DUMMYA_PATH,
            "_scope_": "singleton",
            "_meta1": 999,
            "x": 42,
            "label": "ok",
        }
    }
    dicon = DiContainer(config)

    obj = dicon.get("obj")

    assert isinstance(obj, DummyA)
    assert obj.x == 42
    assert obj.label == "ok"
