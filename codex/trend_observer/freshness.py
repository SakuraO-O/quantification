"""Single owner of completed daily-market observation dates.

Calendar callbacks should use persisted exchange calendars. Missing calendar
entries fall back to weekdays, not invented public-holiday dates.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


MARKET_SESSIONS = {
    "CN": ("Asia/Shanghai", time(15)),
    "HK": ("Asia/Hong_Kong", time(16)),
    "US": ("America/New_York", time(16)),
}


def expected_market_date(market, now, calendar):
    """Return the last completed regular session, honoring DST and holidays.

    An aware timestamp is mandatory. Early closes are conservatively considered
    complete at the regular close until session-level calendars are available.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Market freshness requires a timezone-aware timestamp")
    zone, close = MARKET_SESSIONS[market]
    local = now.astimezone(ZoneInfo(zone))
    candidate = local.date()
    if local.time() < close:
        candidate -= timedelta(days=1)
    for _ in range(370):
        trading = calendar(market, candidate)
        if trading is None:
            trading = candidate.weekday() < 5
        if trading:
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError(f"No completed trading session found for {market}")


def completed_history(frame, expected_date):
    """Drop partial/future sessions before evaluating or persisting a batch."""
    if expected_date is None or frame.empty:
        return frame
    result = frame.loc[frame["date"].dt.date <= expected_date].copy()
    result.attrs.update(frame.attrs)
    return result


def history_is_current(frame, expected_date):
    return not frame.empty and (
        expected_date is None or frame["date"].max().date() == expected_date
    )
