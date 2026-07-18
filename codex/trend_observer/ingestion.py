"""Incremental market/valuation ingestion with watermarks and overlap windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from typing import Callable

import pandas as pd

from .analysis import enrich_history
from .assets import security_id
from .config import MARKET_TIMEZONE
from .data_sources import fetch_history, make_session
from .dividends import apply_dividend_config
from .supabase_store import SupabaseStore, payload_hash


CALCULATION_VERSION = "trend-v2.0.0"


def _as_date(value: str | date | None) -> date | None:
    return pd.Timestamp(value).date() if value else None


def default_is_trading_day(market: str, value: date) -> bool:
    """Safe fallback only; production calendar rows override public holidays."""

    return value.weekday() < 5


def _finite(value):
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else value


def _same_number(left, right) -> bool:
    left, right = _finite(left), _finite(right)
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) < 1e-10


@dataclass
class SyncResult:
    asset: str
    dataset_key: str
    status: str
    rows_received: int = 0
    rows_changed: int = 0
    first_affected_date: str | None = None
    message: str = ""


class MarketSynchronizer:
    """Writes only changed rows and recalculates signals from an overlap point."""

    def __init__(self, store: SupabaseStore, *, is_trading_day: Callable[[str, date], bool] = default_is_trading_day):
        self.store = store
        self.is_trading_day = is_trading_day

    @staticmethod
    def dataset_key(asset: dict) -> str:
        return f"market_daily:{asset['market']}:{asset['symbol']}:{asset['provider']}"

    def _start_date(self, watermark: dict | None) -> date | None:
        latest = _as_date((watermark or {}).get("database_latest_date"))
        # Two overlapping trading days are approximated by four calendar days;
        # market_calendars can later provide exact dates without changing the API.
        return latest - timedelta(days=4) if latest else None

    def sync_asset(self, asset: dict, *, now: datetime | None = None, force: bool = False, run_id: str | None = None) -> SyncResult:
        now = now or datetime.now(MARKET_TIMEZONE)
        asset = apply_dividend_config([asset], allow_subset=True)[0]
        key = self.dataset_key(asset)
        calendar_value = self.store.calendar_is_trading_day(asset["market"], now.date())
        is_trading_day = self.is_trading_day(asset["market"], now.date()) if calendar_value is None else calendar_value
        if not is_trading_day and not force:
            return SyncResult(asset["symbol"], key, "skipped", message="非交易日")
        watermark = self.store.get_watermark(key)
        sid = security_id(asset["symbol"], asset["market"])
        signal_state = self.store.latest_signal_state(sid)

        def signals_current(latest_date: str | None) -> bool:
            return bool(
                latest_date
                and signal_state
                and signal_state.get("calculation_version") == CALCULATION_VERSION
                and signal_state.get("trade_date")
                and _as_date(signal_state["trade_date"]) >= _as_date(latest_date)
            )

        if watermark and watermark.get("status") == "backoff" and watermark.get("next_retry_at") and pd.Timestamp(watermark["next_retry_at"]) > pd.Timestamp(now):
            return SyncResult(asset["symbol"], key, "skipped", message="退避中")
        if (
            not force
            and watermark
            and watermark.get("database_latest_date") == now.date().isoformat()
            and watermark.get("status") == "normal"
            and signals_current(watermark.get("database_latest_date"))
        ):
            return SyncResult(asset["symbol"], key, "skipped", message="当日数据已入库")

        start_date = self._start_date(watermark)
        try:
            with make_session() as session:
                incoming = fetch_history(session, asset, start_date=start_date)
            if incoming.empty:
                raise ValueError("来源未返回有效行情")
            actual_source = incoming.attrs.get("source_provider", asset["provider"])
            incoming = incoming.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
            source_latest_date = incoming.iloc[-1]["date"].date().isoformat()
            incoming_hash = payload_hash(incoming.to_dict("records"))
            if (
                watermark
                and watermark.get("content_hash") == incoming_hash
                and watermark.get("source_latest_date") == source_latest_date
                and signals_current(source_latest_date)
            ):
                self.store.save_watermark(
                    {"dataset_key": key, "last_attempt_at": now.isoformat(), "status": "normal", "consecutive_failures": 0}
                )
                return SyncResult(asset["symbol"], key, "skipped", len(incoming), message="来源未更新")

            existing_records = self.store.history(sid)
            existing_by_date = {row["trade_date"]: row for row in existing_records}
            all_market_rows = [
                {
                    "security_id": sid,
                    "trade_date": row.date.date().isoformat(),
                    "open": _finite(row.open), "high": _finite(row.high), "low": _finite(row.low), "close": _finite(row.close),
                    "volume": _finite(row.volume), "source": actual_source, "adjustment_method": "source",
                }
                for row in incoming.itertuples(index=False)
            ]
            market_rows = [
                row for row in all_market_rows
                if row["trade_date"] not in existing_by_date
                or any(not _same_number(row.get(field), existing_by_date[row["trade_date"]].get(field)) for field in ("open", "high", "low", "close", "volume"))
            ]
            existing_valuations = {row["trade_date"]: row for row in self.store.valuation_history(sid)}
            all_valuation_rows = [
                {
                    "security_id": sid, "trade_date": row.date.date().isoformat(), "valuation_type": "pe",
                    "value": float(row.pe), "source": actual_source, "methodology": "provider_reported",
                }
                for row in incoming.itertuples(index=False)
                if asset["asset_type"] == "指数" and pd.notna(row.pe)
            ]
            valuation_rows = [
                row for row in all_valuation_rows
                if row["trade_date"] not in existing_valuations or not _same_number(row["value"], existing_valuations[row["trade_date"]].get("value"))
            ]
            if not market_rows and not valuation_rows and signals_current(source_latest_date):
                self.store.save_watermark(
                    {"dataset_key": key, "last_attempt_at": now.isoformat(), "source_latest_date": source_latest_date,
                     "database_latest_date": source_latest_date, "content_hash": incoming_hash, "status": "normal", "consecutive_failures": 0}
                )
                return SyncResult(asset["symbol"], key, "skipped", len(incoming), message="来源数据无变化")
            if market_rows:
                self.store.save_market_rows(market_rows)
            if valuation_rows:
                self.store.save_valuation_rows(valuation_rows)

            stored = pd.DataFrame(existing_records)
            if not stored.empty:
                stored["date"] = pd.to_datetime(stored.pop("trade_date"))
                for column in ["open", "high", "low", "close", "volume"]:
                    stored[column] = pd.to_numeric(stored[column], errors="coerce")
                valuation = pd.DataFrame(self.store.valuation_history(sid))
                if valuation.empty:
                    stored["pe"] = math.nan
                else:
                    valuation["date"] = pd.to_datetime(valuation.pop("trade_date"))
                    valuation["pe"] = pd.to_numeric(valuation.pop("value"), errors="coerce")
                    stored = stored.merge(valuation[["date", "pe"]], how="left", on="date")
                history = pd.concat([stored[["date", "open", "high", "low", "close", "volume", "pe"]], incoming], ignore_index=True)
                history = history.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
            else:
                history = incoming
            enriched = enrich_history(history, asset)
            changed_dates = [row["trade_date"] for row in market_rows] + [row["trade_date"] for row in valuation_rows]
            if changed_dates:
                affected_start = pd.Timestamp(min(changed_dates))
            elif not signal_state or signal_state.get("calculation_version") != CALCULATION_VERSION:
                affected_start = pd.Timestamp(enriched.iloc[0]["date"])
            else:
                affected_start = pd.Timestamp(signal_state["trade_date"]) + pd.Timedelta(days=1)
            signal_rows = []
            for row in enriched[enriched["date"] >= affected_start].itertuples(index=False):
                signal_rows.append(
                    {
                        "security_id": sid, "trade_date": row.date.date().isoformat(), "close": _finite(row.close),
                        "daily_return": _finite(row.daily_return), "return_ytd": _finite(row.return_ytd), "return_1w": _finite(row.return_1w),
                        "return_1m": _finite(row.return_1m), "return_1y": _finite(row.return_1y), "return_3y": _finite(row.return_3y),
                        "ma20": _finite(row.MA20), "ma60": _finite(row.MA60), "ma120": _finite(row.MA120), "ma200": _finite(row.MA200),
                        "ma20_slope_5d": _finite(row.ma20_slope_5d), "ma60_slope_10d": _finite(row.ma60_slope_10d),
                        "ma120_slope_20d": _finite(row.ma120_slope_20d), "ma200_slope_40d": _finite(row.ma200_slope_40d),
                        "short_trend": row.short_trend or None, "mid_trend": row.mid_trend or None, "long_trend": row.long_trend or None,
                        "overall_status": row.overall_status or None, "investment_advice": row.investment_advice or None,
                        "pe": _finite(row.pe), "pe_percentile": _finite(row.pe_percentile),
                        "pe_percentile_period": row.pe_percentile_period or None, "valuation_status": row.valuation_status or None,
                        "dividend_yield": _finite(row.dividend_yield), "calculation_version": CALCULATION_VERSION,
                    }
                )
            if signal_rows:
                self.store.save_signal_rows(signal_rows)
            self.store.save_watermark(
                {
                    "dataset_key": key, "last_attempt_at": now.isoformat(), "last_success_at": now.isoformat(),
                    "source_latest_date": source_latest_date, "database_latest_date": source_latest_date,
                    "content_hash": incoming_hash, "status": "normal", "consecutive_failures": 0,
                    "next_retry_at": None, "last_error": None,
                }
            )
            result = SyncResult(asset["symbol"], key, "succeeded", len(incoming), len(market_rows) + len(valuation_rows), affected_start.date().isoformat())
            if run_id:
                self.store.add_run_item(run_id, dataset_key=key, status=result.status, rows_received=result.rows_received, rows_changed=result.rows_changed, first_affected_date=result.first_affected_date)
            return result
        except Exception as exc:
            failures = int((watermark or {}).get("consecutive_failures") or 0) + 1
            backoff_minutes = min(240, 10 * 2 ** max(0, failures - 1))
            status = "backoff" if failures >= 3 else "failed"
            self.store.save_watermark(
                {
                    "dataset_key": key, "last_attempt_at": now.isoformat(), "status": status,
                    "consecutive_failures": failures,
                    "next_retry_at": (now + timedelta(minutes=backoff_minutes)).isoformat(), "last_error": str(exc),
                }
            )
            result = SyncResult(asset["symbol"], key, "failed", message=str(exc))
            if run_id:
                self.store.add_run_item(run_id, dataset_key=key, status="failed", message=str(exc))
            return result

    def sync_assets(self, assets: list[dict], *, trigger_type: str = "schedule", now: datetime | None = None, force: bool = False) -> list[SyncResult]:
        run_id = self.store.start_run("market_sync", trigger_type)
        results = [self.sync_asset(asset, now=now, force=force, run_id=run_id) for asset in assets]
        failed = [item for item in results if item.status == "failed"]
        for item in failed:
            self.store.record_quality_issue(item.dataset_key, "error", "ingestion_failed", {"asset": item.asset, "message": item.message})
        changed = sum(item.rows_changed for item in results)
        self.store.finish_run(run_id, "partial" if failed else "succeeded", {"assets": len(results), "changed_rows": changed, "failed": len(failed)})
        return results
