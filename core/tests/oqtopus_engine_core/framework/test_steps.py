import pytest

from oqtopus_engine_core.framework import (
    DetachOnPostprocess,
    DetachOnPreprocess,
    JoinOnPostprocess,
    JoinOnPreprocess,
    SplitOnPostprocess,
    SplitOnPreprocess,
)

# ================================================================
# Valid combinations (must NOT raise TypeError)
# ================================================================


def test_valid_inherit_split_preprocess():
    """A class inheriting SplitOnPreprocess alone is valid."""

    class MyStep(SplitOnPreprocess):
        pass

    assert True  # No exception means success


def test_valid_inherit_split_postprocess():
    """A class inheriting SplitOnPostprocess alone is valid."""

    class MyStep(SplitOnPostprocess):
        pass

    assert True


def test_valid_inherit_join_preprocess():
    """A class inheriting JoinOnPreprocess alone is valid."""

    class MyStep(JoinOnPreprocess):
        pass

    assert True


def test_valid_inherit_join_postprocess():
    """A class inheriting JoinOnPostprocess alone is valid."""

    class MyStep(JoinOnPostprocess):
        pass

    assert True


def test_valid_inherit_no_mixins():
    """A class with no mixins should be valid."""

    class MyStep:
        pass

    assert True


def test_valid_cross_phase_mixins_split_join():
    """
    SplitOnPreprocess + JoinOnPostprocess is allowed,
    because they belong to different process phases.
    """

    class MyStep(SplitOnPreprocess, JoinOnPostprocess):
        pass

    assert True


def test_valid_cross_phase_mixins_join_split():
    """
    JoinOnPreprocess + SplitOnPostprocess is allowed,
    because they belong to different process phases.
    """

    class MyStep(JoinOnPreprocess, SplitOnPostprocess):
        pass

    assert True


def test_valid_inherit_detach_preprocess():
    """A class inheriting DetachOnPreprocess alone is valid."""

    class MyStep(DetachOnPreprocess):
        pass

    assert True


def test_valid_inherit_detach_postprocess():
    """A class inheriting DetachOnPostprocess alone is valid."""

    class MyStep(DetachOnPostprocess):
        pass

    assert True


def test_valid_cross_phase_detach_and_split():
    """
    DetachOnPreprocess + SplitOnPostprocess is allowed because they belong
    to different phases.
    """

    class MyStep(DetachOnPreprocess, SplitOnPostprocess):
        pass

    assert True


def test_valid_cross_phase_detach_and_join():
    """
    DetachOnPostprocess + JoinOnPreprocess is allowed because they belong
    to different phases.
    """

    class MyStep(JoinOnPreprocess, DetachOnPostprocess):
        pass

    assert True


# ================================================================
# Invalid combinations (must raise TypeError)
# ================================================================


def test_invalid_split_and_join_preprocess():
    """
    SplitOnPreprocess and JoinOnPreprocess are mutually exclusive.
    Defining a class with both must raise TypeError.
    """
    with pytest.raises(TypeError):

        class BadStep(SplitOnPreprocess, JoinOnPreprocess):
            pass


def test_invalid_split_and_join_postprocess():
    """
    SplitOnPostprocess and JoinOnPostprocess are mutually exclusive.
    Defining a class with both must raise TypeError.
    """
    with pytest.raises(TypeError):

        class BadStep(SplitOnPostprocess, JoinOnPostprocess):
            pass


def test_invalid_order_reverse_preprocess():
    """
    The reverse inheritance order must also raise TypeError.
    (JoinOnPreprocess + SplitOnPreprocess)
    """
    with pytest.raises(TypeError):

        class BadStep(JoinOnPreprocess, SplitOnPreprocess):
            pass


def test_invalid_order_reverse_postprocess():
    """
    The reverse inheritance order must also raise TypeError.
    (JoinOnPostprocess + SplitOnPostprocess)
    """
    with pytest.raises(TypeError):

        class BadStep(JoinOnPostprocess, SplitOnPostprocess):
            pass


def test_invalid_split_and_detach_preprocess():
    """
    SplitOnPreprocess and DetachOnPreprocess are mutually exclusive.
    """
    with pytest.raises(TypeError):

        class BadStep(SplitOnPreprocess, DetachOnPreprocess):
            pass


def test_invalid_join_and_detach_preprocess():
    """
    JoinOnPreprocess and DetachOnPreprocess are mutually exclusive.
    """
    with pytest.raises(TypeError):

        class BadStep(JoinOnPreprocess, DetachOnPreprocess):
            pass


def test_invalid_detach_preprocess_reverse_order():
    """
    Reverse inheritance order must also raise TypeError.
    (DetachOnPreprocess + SplitOnPreprocess)
    """
    with pytest.raises(TypeError):

        class BadStep(DetachOnPreprocess, SplitOnPreprocess):
            pass


def test_invalid_split_and_detach_postprocess():
    """
    SplitOnPostprocess and DetachOnPostprocess are mutually exclusive.
    """
    with pytest.raises(TypeError):

        class BadStep(SplitOnPostprocess, DetachOnPostprocess):
            pass


def test_invalid_join_and_detach_postprocess():
    """
    JoinOnPostprocess and DetachOnPostprocess are mutually exclusive.
    """
    with pytest.raises(TypeError):

        class BadStep(JoinOnPostprocess, DetachOnPostprocess):
            pass


def test_invalid_detach_postprocess_reverse_order():
    """
    Reverse inheritance order must also raise TypeError.
    (DetachOnPostprocess + SplitOnPostprocess)
    """
    with pytest.raises(TypeError):

        class BadStep(DetachOnPostprocess, SplitOnPostprocess):
            pass
