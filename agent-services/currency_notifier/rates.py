"""Frankfurter API client and rate persistence for ``currency-notifier``.

Provides:

* :func:`fetch_rate` — async one-shot fetch from the public Frankfurter API.
* :class:`RateStore` — SQLite-backed history of observed rates plus a small
  dedup helper (:meth:`RateStore.should_notify`) used by the pipeline to
  avoid double-notifying when the upstream rate has not changed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from common.database import Database
from common.exceptions import FetchError

logger = logging.getLogger(__name__)

#: Frankfurter public API endpoint. The service updates rates once per business
#: day, so polling more often than daily just yields the same numbers.
FRANKFURTER_URL = "https://api.frankfurter.app/latest"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS currency_rates (
    pair TEXT NOT NULL,
    rate REAL NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (pair, timestamp)
)
"""

_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_currency_rates_pair_ts "
    "ON currency_rates (pair, timestamp)"
)


# ---------------------------------------------------------------------------
# Frankfurter client
# ---------------------------------------------------------------------------


async def fetch_rate(
    base: str,
    quote: str,
    client: httpx.AsyncClient,
) -> float | None:
    """Fetch the latest exchange rate from Frankfurter.

    Parameters
    ----------
    base, quote:
        ISO-4217 currency codes (case-insensitive).
    client:
        An :class:`httpx.AsyncClient` (typically produced by
        :func:`common.http_client.create_async_client` so retries are
        automatic).

    Returns
    -------
    ``float`` rate (how many ``quote`` units one ``base`` buys) or ``None``
    if the request failed non-catastrophically (network error, malformed
    JSON, unexpected status code).

    Raises
    ------
    FetchError
        If Frankfurter returns HTTP 404 — that signals an unknown pair and
        is treated as a programmer error rather than a transient failure.
    """
    # Normalise to uppercase so callers may pass either case without
    # generating a different cache key per call.
    base = base.strip().upper()
    quote = quote.strip().upper()
    url = f"{FRANKFURTER_URL}?from={base}&to={quote}"

    try:
        response = await client.get(url)
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RequestError) as exc:
        logger.warning("Frankfurter fetch failed for %s/%s: %s", base, quote, exc)
        return None

    if response.status_code == 404:
        # Unknown currency pair is a permanent failure — surface as FetchError
        # so callers can fail loudly during configuration mistakes.
        raise FetchError(
            f"Frankfurter returned 404 for {base}/{quote}: "
            f"unknown currency pair"
        )

    if response.status_code >= 400:
        logger.warning(
            "Frankfurter returned HTTP %s for %s/%s",
            response.status_code,
            base,
            quote,
        )
        return None

    try:
        payload: Any = response.json()
    except ValueError as exc:
        logger.warning("Frankfurter returned non-JSON body: %s", exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Frankfurter payload is not a JSON object: %r", payload)
        return None

    rates = payload.get("rates")
    if not isinstance(rates, dict):
        logger.warning("Frankfurter payload missing 'rates' object: %r", payload)
        return None

    raw_rate = rates.get(quote)
    if raw_rate is None:
        logger.warning(
            "Frankfurter response missing rate for %s in payload: %r", quote, rates
        )
        return None

    try:
        return float(raw_rate)
    except (TypeError, ValueError) as exc:
        logger.warning("Frankfurter rate %r is not numeric: %s", raw_rate, exc)
        return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string produced by :func:`_utcnow_iso` or stdlib."""
    # ``datetime.fromisoformat`` accepts the ``+00:00`` suffix; convert any
    # trailing ``Z`` (used by some upstream writers) for safety.
    cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class RateStore:
    """SQLite-backed history of observed currency rates.

    The schema is a single table ``currency_rates(pair, rate, timestamp)``
    keyed by ``(pair, timestamp)`` so re-storing the same pair within the
    same second is idempotent (``INSERT OR REPLACE``), while distinct
    timestamps grow the history.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._db.execute_write(_SCHEMA_SQL)
        self._db.execute_write(_INDEX_SQL)

    async def store_rate(self, pair: str, rate: float) -> None:
        """Insert (or overwrite) the latest rate observation for ``pair``."""
        ts = _utcnow_iso()
        self._db.execute_write(
            "INSERT OR REPLACE INTO currency_rates (pair, rate, timestamp) "
            "VALUES (?, ?, ?)",
            (pair, float(rate), ts),
        )

    async def get_recent_rates(
        self, pair: str, days: int = 30
    ) -> list[tuple[float, datetime]]:
        """Return all rate observations for ``pair`` from the last ``days``.

        Rows are returned in ascending chronological order so callers can use
        the list directly as a time series (last element is most recent).
        Unparseable timestamps are skipped defensively.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._db.execute(
            "SELECT rate, timestamp FROM currency_rates "
            "WHERE pair = ? AND timestamp >= ? "
            "ORDER BY timestamp ASC",
            (pair, cutoff),
        )

        out: list[tuple[float, datetime]] = []
        for row in rows:
            ts_raw = row.get("timestamp", "")
            try:
                ts = _parse_iso(str(ts_raw))
            except ValueError:
                logger.warning("Skipping rate row with unparseable timestamp: %r", ts_raw)
                continue
            out.append((float(row["rate"]), ts))
        return out

    async def get_last_rate(self, pair: str) -> float | None:
        """Return the most-recent rate observed for ``pair``, or ``None``."""
        rows = self._db.execute(
            "SELECT rate FROM currency_rates WHERE pair = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (pair,),
        )
        if not rows:
            return None
        return float(rows[0]["rate"])

    async def should_notify(self, pair: str, rate: float) -> bool:
        """Decide whether ``rate`` warrants a new notification.

        * First observation for the pair → ``True`` (always notify).
        * Subsequent observations → ``True`` only when the rate differs from
          the most recently stored value. This dedups repeated polls that
          yield the same Frankfurter figure.
        """
        last = await self.get_last_rate(pair)
        if last is None:
            return True
        return last != float(rate)


__all__ = [
    "FRANKFURTER_URL",
    "RateStore",
    "fetch_rate",
]
