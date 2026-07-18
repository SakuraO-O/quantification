"""Create immutable dashboard versions from normalized Supabase facts."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .allocation import allocation_summary, calculate_allocation
from .config import ASSETS, MARKET_TIMEZONE, PORTFOLIO_CATEGORIES, STYLE_COMPASS_PAIRS
from .ingestion import CALCULATION_VERSION
from .style_compass import calculate_style_compass, style_recommendation
from .supabase_store import SupabaseStore


class DashboardPublisher:
    def __init__(self, store: SupabaseStore):
        self.store = store

    def _active_assets(self) -> list[dict]:
        return self.store.select("securities", filters={"is_active": "eq.true"}, order="asset_type.asc,name.asc")

    def _latest_signal(self, sid: str) -> dict | None:
        rows = self.store.select("asset_daily_signals", filters={"security_id": f"eq.{sid}"}, order="trade_date.desc", limit=1)
        return rows[0] if rows else None

    def _latest_dividend(self, sid: str) -> dict | None:
        rows = self.store.select(
            "dividend_events",
            select="fiscal_year,cash_dividend_per_share,announcement_date,announcement_id,event_stage",
            filters={"security_id": f"eq.{sid}", "event_stage": "eq.implemented"},
            order="fiscal_year.desc,announcement_date.desc",
            limit=1,
        )
        return rows[0] if rows else None

    def _latest_fundamental_assessment(self, sid: str) -> dict | None:
        rows = self.store.select(
            "fundamental_assessments",
            select=(
                "report_period,dividend_safety_status,operating_quality_status,"
                "cash_reinvestment_status,capital_structure_status,fundamental_status,"
                "evidence,main_risk,calculation_version,created_at"
            ),
            filters={"security_id": f"eq.{sid}"},
            order="report_period.desc,created_at.desc",
            limit=1,
        )
        return rows[0] if rows else None

    def _allocation(self) -> dict | None:
        rows = self.store.select("portfolio_allocations", order="version.desc,data_date.desc")
        latest: dict[tuple[str, str], dict] = {}
        for row in rows:
            latest.setdefault((row["allocation_type"], row["category"]), row)
        if len(latest) != len(PORTFOLIO_CATEGORIES) * 2:
            return None
        targets = {category: float(latest[("target_ratio", category)]["value"]) for category in PORTFOLIO_CATEGORIES}
        actuals = {category: float(latest[("actual_amount", category)]["value"]) for category in PORTFOLIO_CATEGORIES}
        values = calculate_allocation(targets, actuals)
        return {"rows": values, "summary": allocation_summary(values)}

    def _style_compass(self, assets_by_name: dict[str, dict]) -> list[dict]:
        result: list[dict] = []
        for left_name, right_name in STYLE_COMPASS_PAIRS:
            left_asset, right_asset = assets_by_name.get(left_name), assets_by_name.get(right_name)
            if not left_asset or not right_asset:
                continue
            left_history = pd.DataFrame(self.store.history(left_asset["security_id"]))
            right_history = pd.DataFrame(self.store.history(right_asset["security_id"]))
            if left_history.empty or right_history.empty:
                continue
            left_history = left_history.rename(columns={"trade_date": "date"})
            right_history = right_history.rename(columns={"trade_date": "date"})
            compass = calculate_style_compass(left_history, right_history)
            left_signal, right_signal = self._latest_signal(left_asset["security_id"]), self._latest_signal(right_asset["security_id"])
            compass |= {
                "left": {"name": left_name, "pe_percentile": (left_signal or {}).get("pe_percentile"), "investment_advice": (left_signal or {}).get("investment_advice")},
                "right": {"name": right_name, "pe_percentile": (right_signal or {}).get("pe_percentile"), "investment_advice": (right_signal or {}).get("investment_advice")},
            }
            compass["recommendation"] = style_recommendation(
                str(compass["direction"]), str((left_signal or {}).get("investment_advice")), str((right_signal or {}).get("investment_advice"))
            )
            result.append(compass)
        return result

    def publish(self, source_run_id: str | None = None) -> dict:
        assets = self._active_assets()
        records = []
        incomplete = []
        now = datetime.now(MARKET_TIMEZONE)
        expected_symbols = {asset["symbol"] for asset in ASSETS}
        actual_symbols = {asset["symbol"] for asset in assets}
        if actual_symbols != expected_symbols or len(assets) != len(ASSETS):
            incomplete.append(
                {
                    "reason": "资产清单不一致",
                    "missing": sorted(expected_symbols - actual_symbols),
                    "unexpected": sorted(actual_symbols - expected_symbols),
                }
            )
        for asset in assets:
            signal = self._latest_signal(asset["security_id"])
            if not signal:
                incomplete.append({"symbol": asset["symbol"], "reason": "缺少派生信号"})
                signal = {"trade_date": None, "overall_status": "数据不足", "investment_advice": "数据不足"}
            else:
                expected_date = self.store.previous_trading_date(asset["market"], now.date())
                actual_date = pd.Timestamp(signal["trade_date"]).date()
                if actual_date < expected_date:
                    incomplete.append({"symbol": asset["symbol"], "reason": "行情日期滞后", "expected": expected_date.isoformat(), "actual": actual_date.isoformat()})
            record = {
                "name": asset["name"], "symbol": asset["symbol"], "market": asset["market"], "asset_type": asset["asset_type"],
                "industry_template": asset.get("industry_template"),
            } | signal
            if asset["asset_type"] == "股票":
                dividend = self._latest_dividend(asset["security_id"])
                assessment = self._latest_fundamental_assessment(asset["security_id"])
                record |= {
                    "last_year_dividend": (dividend or {}).get("cash_dividend_per_share"),
                    "latest_announcement_date": (dividend or {}).get("announcement_date"),
                    "fundamental_assessment": assessment,
                }
            records.append(record)
        assets_by_name = {asset["name"]: asset for asset in assets}
        dates = [record.get("trade_date") for record in records if record.get("trade_date")]
        allocation = self._allocation()
        payload = {
            "schema_version": 2,
            "latest_market_date": max(dates) if dates else None,
            "assets": records,
            "allocation": allocation,
            "style_compass": self._style_compass(assets_by_name),
        }
        completeness = {"missing_asset_signals": incomplete, "allocation_configured": allocation is not None}
        # Allocation is editable user configuration, not a market-data blocker.
        is_complete = not incomplete
        return self.store.publish_dashboard_version(
            payload, is_complete=is_complete, completeness=completeness, calculation_version=CALCULATION_VERSION, source_run_id=source_run_id
        )
