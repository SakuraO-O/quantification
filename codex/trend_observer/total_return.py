"""Authoritative stock-only, three-calendar-year return calculation.

The market/trend series is qfq.  It is used solely to detect unexplained
corporate actions; neither published return adds cash to an adjusted close.
"""

from __future__ import annotations

from datetime import date, datetime
import math


MAX_START_LAG_DAYS = 15
MAX_OBSERVATION_GAP_DAYS = 20
MAX_UNEXPLAINED_ADJUSTMENT_CHANGE = 0.0075


def _day(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _nonnegative(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _three_years_ago(end: date) -> date:
    try:
        return end.replace(year=end.year - 3)
    except ValueError:  # February 29
        return end.replace(year=end.year - 3, day=28)


def stock_three_year_returns(market_rows: list[dict], dividend_rows: list[dict],
                             end_date: str | date, *, dividend_history_current: bool) -> dict:
    """Return price-only and gross reinvested-dividend returns or a clear gap.

    The first close is the latest trading close on/before the calendar date
    three years earlier.  Cash and bonus shares are attributed on ex-date;
    the daily gross-return factor is (raw_close * share_factor + cash) /
    previous_raw_close.  Rights issues/unrecorded actions are not inferred.
    """

    output = {
        "price_return_3y": None, "total_return_3y": None,
        "total_return_3y_status": "数据不足", "total_return_3y_issue": "近三年原始行情或权益事件未核验",
        "total_return_3y_start_date": None, "total_return_3y_end_date": str(end_date)[:10],
        "total_return_3y_method": "tax_before_dividend_reinvested_on_ex_date",
    }
    end = _day(end_date)
    if end is None or not dividend_history_current:
        return output
    target = _three_years_ago(end)
    rows = sorted(((day, row) for row in market_rows if (day := _day(row.get("trade_date"))) and day <= end),
                  key=lambda item: item[0])
    starts = [item for item in rows if item[0] <= target]
    if not starts or (target - starts[-1][0]).days > MAX_START_LAG_DAYS or not rows or rows[-1][0] != end:
        return output
    start = starts[-1][0]
    path = [(day, row) for day, row in rows if start <= day <= end]
    output["total_return_3y_start_date"] = start.isoformat()
    if len(path) < 2 or any((later[0] - earlier[0]).days > MAX_OBSERVATION_GAP_DAYS
                            for earlier, later in zip(path, path[1:])):
        output["total_return_3y_issue"] = "近三年行情存在缺口"
        return output
    if any(_positive(row.get("raw_close")) is None or _positive(row.get("close")) is None
           or row.get("raw_close_source") not in {"eastmoney", "tencent"} or row.get("adjustment_method") != "qfq"
           for _, row in path):
        output["total_return_3y_issue"] = "未复权与复权价格尚未完整回填"
        return output

    events: dict[date, dict] = {}
    for event in dividend_rows:
        if event.get("event_stage") != "implemented":
            continue
        ex_date = _day(event.get("ex_date"))
        announcement = _day(event.get("announcement_date"))
        if ex_date is None:
            if announcement and start < announcement <= end:
                output["total_return_3y_issue"] = "已实施权益事件缺少除权除息日"
                return output
            continue
        if not start < ex_date <= end:
            continue
        if ex_date in events:
            output["total_return_3y_issue"] = "同一除权除息日存在待核验的重复事件"
            return output
        if any(_nonnegative(event.get(field)) is None for field in (
            "cash_dividend_per_share", "bonus_shares_per_share", "transfer_shares_per_share"
        )):
            output["total_return_3y_issue"] = "分红或送转股比例未完整回填"
            return output
        events[ex_date] = event
    if not set(events).issubset({day for day, _ in path}):
        output["total_return_3y_issue"] = "除权除息日缺少对应收盘价"
        return output

    price_factor = total_factor = 1.0
    _, previous = path[0]
    for day, row in path[1:]:
        event = events.get(day, {})
        share_factor = 1 + float(event.get("bonus_shares_per_share") or 0) + float(event.get("transfer_shares_per_share") or 0)
        cash = float(event.get("cash_dividend_per_share") or 0)
        raw_previous, raw_current = float(previous["raw_close"]), float(row["raw_close"])
        adjusted_ratio_previous = float(previous["close"]) / raw_previous
        adjusted_ratio_current = float(row["close"]) / raw_current
        expected_ratio_change = raw_previous * share_factor / (raw_previous - cash) if raw_previous > cash else None
        observed_ratio_change = adjusted_ratio_current / adjusted_ratio_previous
        if expected_ratio_change is None or abs(observed_ratio_change / expected_ratio_change - 1) > MAX_UNEXPLAINED_ADJUSTMENT_CHANGE:
            output["total_return_3y_issue"] = "检测到未解释的复权变化，可能存在配股或缺失权益事件"
            return output
        price_factor *= raw_current * share_factor / raw_previous
        total_factor *= (raw_current * share_factor + cash) / raw_previous
        previous = row
    if not math.isfinite(price_factor) or not math.isfinite(total_factor):
        return output
    output.update({
        "price_return_3y": price_factor - 1, "total_return_3y": total_factor - 1,
        "total_return_3y_status": "可用", "total_return_3y_issue": None,
    })
    return output
