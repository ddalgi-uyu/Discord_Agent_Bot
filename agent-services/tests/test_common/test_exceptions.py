"""Unit tests for :mod:`common.exceptions`."""

from __future__ import annotations

import pytest

from common.exceptions import (
    AnalysisError,
    AppError,
    ConfigError,
    FetchError,
    NotifyError,
)


@pytest.mark.parametrize(
    "exc_cls",
    [ConfigError, NotifyError, FetchError, AnalysisError],
    ids=["ConfigError", "NotifyError", "FetchError", "AnalysisError"],
)
def test_subclass_inherits_from_app_error(exc_cls: type[AppError]) -> None:
    """All concrete error classes must derive from :class:`AppError`."""
    assert issubclass(exc_cls, AppError)
    assert issubclass(exc_cls, Exception)


def test_app_error_inherits_from_exception() -> None:
    """``AppError`` itself must be a regular :class:`Exception` subclass."""
    assert issubclass(AppError, Exception)


def test_app_error_is_base_for_all_concrete_errors() -> None:
    """Every concrete error must be catchable via :class:`AppError`."""
    concrete = (ConfigError, NotifyError, FetchError, AnalysisError)
    for cls in concrete:
        assert cls.__mro__.count(AppError) >= 1, f"{cls.__name__} missing AppError in MRO"


@pytest.mark.parametrize(
    ("exc_cls", "message"),
    [
        (AppError, "boom"),
        (ConfigError, "missing config file"),
        (NotifyError, "discord webhook rejected"),
        (FetchError, "network timeout"),
        (AnalysisError, "division by zero"),
    ],
)
def test_str_includes_class_name_and_message(
    exc_cls: type[AppError], message: str
) -> None:
    """``__str__`` must include the class name and the original message."""
    err = exc_cls(message)
    rendered = str(err)
    assert err.message == message
    assert exc_cls.__name__ in rendered
    assert message in rendered
    # Default Exception str is "<class>: <msg>" — assert the format.
    assert rendered == f"{exc_cls.__name__}: {message}"


def test_str_for_subclass_uses_subclass_name_not_base() -> None:
    """A :class:`ConfigError` rendered via the subclass must show ``ConfigError``."""
    err = ConfigError("bad yaml")
    assert str(err).startswith("ConfigError:")


def test_exception_args_populated() -> None:
    """The original ``Exception.args`` contract is preserved for tooling."""
    err = FetchError("upstream 503")
    assert err.args == ("upstream 503",)
    assert err.message == "upstream 503"


def test_catchable_via_app_error() -> None:
    """Concrete errors must be catchable via :class:`AppError`."""
    with pytest.raises(AppError) as excinfo:
        raise ConfigError("nope")
    assert isinstance(excinfo.value, ConfigError)
    assert "ConfigError" in str(excinfo.value)
    assert "nope" in str(excinfo.value)


def test_catchable_via_exception() -> None:
    """Concrete errors must remain catchable via :class:`Exception`."""
    with pytest.raises(Exception) as excinfo:
        raise NotifyError("delivery failed")
    assert excinfo.value.message == "delivery failed"


def test_each_concrete_error_can_be_caught_by_app_error() -> None:
    """A single ``except AppError`` clause catches every concrete error."""

    cases = [
        (ConfigError, "c"),
        (NotifyError, "n"),
        (FetchError, "f"),
        (AnalysisError, "a"),
    ]
    for cls, msg in cases:
        with pytest.raises(AppError) as excinfo:
            raise cls(msg)
        assert isinstance(excinfo.value, cls)
        assert str(excinfo.value) == f"{cls.__name__}: {msg}"


def test_default_message_used_when_omitted() -> None:
    """Constructing an error with no message must not crash and must default."""
    err = ConfigError()
    assert err.message == "configuration error"
    assert str(err) == "ConfigError: configuration error"


def test_message_attribute_is_set_explicitly() -> None:
    """``message`` attribute is exposed for callers that prefer it over ``str()``."""
    err = AnalysisError("NaN in series")
    assert err.message == "NaN in series"


def test_can_be_used_in_except_chain() -> None:
    """``raise ... from ...`` must produce useful chained output."""
    try:
        try:
            raise ValueError("inner")
        except ValueError as inner:
            raise FetchError("outer") from inner
    except FetchError as outer:
        assert outer.__cause__ is not None
        assert isinstance(outer.__cause__, ValueError)
        assert str(outer) == "FetchError: outer"


def test_mro_is_well_formed() -> None:
    """Each error's MRO must be ``cls -> AppError -> Exception -> BaseException -> object``."""
    for cls in (ConfigError, NotifyError, FetchError, AnalysisError):
        mro_names = [c.__name__ for c in cls.__mro__]
        assert mro_names[0] == cls.__name__
        assert "AppError" in mro_names
        assert "Exception" in mro_names
