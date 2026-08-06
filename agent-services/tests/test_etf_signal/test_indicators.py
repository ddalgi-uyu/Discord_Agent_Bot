"""Tests for :mod:`etf_signal.indicators`.

The heavy third-party deps (``yfinance``, ``pandas_ta``, ``pandas``) are
not installed in the test environment. We monkeypatch the module-level
references and feed in a small :class:`FakeDataFrame` that duck-types the
pandas interface we actually use. No real network calls are made.
"""
from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from etf_signal import indicators as indicators_module
from etf_signal.config import EtfSignalConfig
from etf_signal.indicators import compute_indicators, validate_dataframe


# ---------------------------------------------------------------------------
# Fake pandas-shaped objects
# ---------------------------------------------------------------------------


class FakeSeries:
    """A minimal pandas-``Series`` look-alike.

    Implements only the surface :mod:`etf_signal.indicators` actually uses:
    ``isna().any()``, ``astype(float)``, ``iloc[-1]``, and iteration. The
    ``has_nan`` / ``has_inf`` constructor flags let each test pick the
    behaviour it wants to exercise.
    """

    def __init__(
        self,
        values: list[float],
        *,
        has_nan: bool = False,
        has_inf: bool = False,
    ) -> None:
        self._values: list[float] = list(values)
        self._has_nan = has_nan
        self._has_inf = has_inf

    def __iter__(self) -> Iterator[float]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def isna(self) -> Any:
        """Return a ``.any()``-bearing view that mirrors pandas' ``isna()``."""
        outer = self

        class _IsNaView:
            def any(inner) -> bool:  # noqa: N805 — nested helper
                return outer._has_nan

        return _IsNaView()

    def astype(self, _dtype: Any) -> "FakeSeries":
        return self

    @property
    def iloc(self) -> Any:
        outer = self

        class _IlocView:
            def __getitem__(inner, idx: int) -> float:  # noqa: N805
                return outer._values[idx]

        return _IlocView()


class FakeDataFrame:
    """Minimal pandas-``DataFrame`` look-alike."""

    def __init__(
        self,
        close_values: list[float],
        *,
        columns: list[str] | None = None,
        empty: bool = False,
        has_nan: bool = False,
        has_inf: bool = False,
    ) -> None:
        self._close = FakeSeries(
            close_values, has_nan=has_nan, has_inf=has_inf
        )
        self._columns: list[str] = (
            list(columns) if columns is not None
            else ["Close", "Open", "High", "Low", "Volume"]
        )
        self._empty = empty

    @property
    def empty(self) -> bool:
        return self._empty

    @property
    def columns(self) -> list[str]:
        return self._columns

    def __getitem__(self, key: str) -> FakeSeries:
        if key == "Close":
            return self._close
        raise KeyError(key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_series(value: float) -> FakeSeries:
    """A FakeSeries whose ``iloc[-1]`` is ``value``."""
    return FakeSeries([value])


class FakeBBandsDF(FakeDataFrame):
    """A :class:`FakeDataFrame` shaped like ``pandas_ta.bbands`` output.

    Overrides ``columns`` and ``__getitem__`` at the class level so the
    special-method dispatch that Python uses for ``df[key]`` works
    correctly (assigning to ``__dict__["__getitem__"]`` on an instance is
    silently ignored for special methods, so we have to subclass).
    """

    _BBANDS_COLUMNS: list[str] = [
        "BBL_20_2.0", "BBM_20_2.0", "BBU_20_2.0", "BBB_20_2.0", "BBP_20_2.0"
    ]

    def __init__(self, lower: float, mid: float, upper: float) -> None:
        super().__init__([100.0], columns=list(self._BBANDS_COLUMNS))
        self._bb_lower = _make_series(lower)
        self._bb_mid = _make_series(mid)
        self._bb_upper = _make_series(upper)

    def __getitem__(self, key: str) -> FakeSeries:  # type: ignore[override]
        if key == "BBL_20_2.0":
            return self._bb_lower
        if key == "BBM_20_2.0":
            return self._bb_mid
        if key == "BBU_20_2.0":
            return self._bb_upper
        if key == "Close":
            return self._close
        raise KeyError(key)


def _make_bbands_df(lower: float, mid: float, upper: float) -> FakeBBandsDF:
    """A FakeDataFrame shaped like ``pandas_ta.bbands`` output."""
    return FakeBBandsDF(lower, mid, upper)


def _stub_yfinance(df: Any) -> MagicMock:
    """Build a stub ``yf`` module whose ``download`` returns ``df``."""
    stub = MagicMock(name="yfinance")
    stub.download.return_value = df
    return stub


def _stub_pandas_ta(
    *,
    rsi: float,
    sma20: float,
    sma50: float,
    sma200: float,
    bb_lower: float,
    bb_mid: float,
    bb_upper: float,
) -> MagicMock:
    """Build a stub ``pandas_ta`` module with the expected API surface."""
    stub = MagicMock(name="pandas_ta")

    def _rsi(close: Any, length: int) -> FakeSeries:
        return _make_series(rsi)

    def _sma(close: Any, length: int) -> FakeSeries:
        mapping = {20: sma20, 50: sma50, 200: sma200}
        return _make_series(mapping.get(length, sma50))

    def _bbands(close: Any, length: int, std: float) -> FakeDataFrame:
        return _make_bbands_df(bb_lower, bb_mid, bb_upper)

    stub.rsi.side_effect = _rsi
    stub.sma.side_effect = _sma
    stub.bbands.side_effect = _bbands
    return stub


def _stub_etf_signal_config() -> EtfSignalConfig:
    """Minimal config carrying only the indicators block."""
    return EtfSignalConfig(
        app_name="etf-signal",
        symbols=["SPY"],
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateDataframe:
    """Direct coverage for the DataFrame validator."""

    def test_none_dataframe_returns_false(self) -> None:
        assert validate_dataframe(None, "SPY") is False

    def test_empty_dataframe_returns_false(self) -> None:
        df = FakeDataFrame([], empty=True)
        assert validate_dataframe(df, "SPY") is False

    def test_missing_close_column_returns_false(self) -> None:
        df = FakeDataFrame([1.0, 2.0], columns=["Open", "High"])
        assert validate_dataframe(df, "SPY") is False

    def test_nan_in_close_returns_false(self) -> None:
        df = FakeDataFrame([1.0, 2.0], has_nan=True)
        assert validate_dataframe(df, "SPY") is False

    def test_positive_inf_in_close_returns_false(self) -> None:
        df = FakeDataFrame([1.0, float("inf")], has_inf=True)
        assert validate_dataframe(df, "SPY") is False

    def test_negative_inf_in_close_returns_false(self) -> None:
        df = FakeDataFrame([1.0, -float("inf")], has_inf=True)
        assert validate_dataframe(df, "SPY") is False

    def test_valid_dataframe_returns_true(self) -> None:
        df = FakeDataFrame([10.0, 11.0, 12.0, 13.0])
        assert validate_dataframe(df, "SPY") is True


# ---------------------------------------------------------------------------
# compute_indicators
# ---------------------------------------------------------------------------


class TestComputeIndicators:
    """End-to-end coverage for the async compute path."""

    @pytest.mark.asyncio
    async def test_returns_none_when_yfinance_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(indicators_module, "yf", None)
        monkeypatch.setattr(indicators_module, "ta", MagicMock())
        result = await compute_indicators("SPY", _stub_etf_signal_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_pandas_ta_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(indicators_module, "yf", MagicMock())
        monkeypatch.setattr(indicators_module, "ta", None)
        result = await compute_indicators("SPY", _stub_etf_signal_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_yfinance_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yf_stub = MagicMock()
        yf_stub.download.side_effect = RuntimeError("network down")
        monkeypatch.setattr(indicators_module, "yf", yf_stub)
        monkeypatch.setattr(indicators_module, "ta", MagicMock())

        result = await compute_indicators("SPY", _stub_etf_signal_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_dataframe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df = FakeDataFrame([], empty=True)
        monkeypatch.setattr(indicators_module, "yf", _stub_yfinance(df))
        monkeypatch.setattr(indicators_module, "ta", MagicMock())

        result = await compute_indicators("SPY", _stub_etf_signal_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_nan_close(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df = FakeDataFrame([1.0, 2.0, 3.0], has_nan=True)
        monkeypatch.setattr(indicators_module, "yf", _stub_yfinance(df))
        monkeypatch.setattr(indicators_module, "ta", MagicMock())

        result = await compute_indicators("SPY", _stub_etf_signal_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_inf_close(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df = FakeDataFrame([1.0, 2.0, float("inf")], has_inf=True)
        monkeypatch.setattr(indicators_module, "yf", _stub_yfinance(df))
        monkeypatch.setattr(indicators_module, "ta", MagicMock())

        result = await compute_indicators("SPY", _stub_etf_signal_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_indicator_dict_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        close_values = [100.0, 101.0, 102.0, 103.0, 104.0]
        df = FakeDataFrame(close_values)
        ta_stub = _stub_pandas_ta(
            rsi=28.5,
            sma20=99.5,
            sma50=98.7,
            sma200=95.0,
            bb_lower=97.5,
            bb_mid=101.0,
            bb_upper=104.5,
        )

        monkeypatch.setattr(indicators_module, "yf", _stub_yfinance(df))
        monkeypatch.setattr(indicators_module, "ta", ta_stub)

        result = await compute_indicators("SPY", _stub_etf_signal_config())

        assert result is not None
        assert result["symbol"] == "SPY"
        assert result["price"] == pytest.approx(104.0)
        assert result["rsi"] == pytest.approx(28.5)
        assert result["sma_20"] == pytest.approx(99.5)
        assert result["sma_50"] == pytest.approx(98.7)
        assert result["sma_200"] == pytest.approx(95.0)
        assert result["bb_lower"] == pytest.approx(97.5)
        assert result["bb_mid"] == pytest.approx(101.0)
        assert result["bb_upper"] == pytest.approx(104.5)

        # Sanity: pandas_ta.bbands was called with the configured params.
        ta_stub.bbands.assert_called_once()
        kwargs = ta_stub.bbands.call_args.kwargs
        assert kwargs["length"] == 20
        assert kwargs["std"] == 2.0

    @pytest.mark.asyncio
    async def test_bbands_with_unexpected_columns_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If pandas_ta renames its BB columns, we fail closed."""
        df = FakeDataFrame([1.0, 2.0, 3.0])
        # Build a custom bbands stub that returns columns we don't expect.
        ta_stub = MagicMock(name="pandas_ta")
        ta_stub.rsi.return_value = _make_series(50.0)
        ta_stub.sma.return_value = _make_series(100.0)

        class WeirdBBandsDF(FakeDataFrame):
            def __init__(self) -> None:
                super().__init__([1.0])
                self._columns = ["foo", "bar"]

            def __getitem__(self, key: str) -> FakeSeries:  # type: ignore[override]
                raise KeyError(key)

        weird = WeirdBBandsDF()
        ta_stub.bbands.return_value = weird

        monkeypatch.setattr(indicators_module, "yf", _stub_yfinance(df))
        monkeypatch.setattr(indicators_module, "ta", ta_stub)

        result = await compute_indicators("SPY", _stub_etf_signal_config())
        assert result is None
