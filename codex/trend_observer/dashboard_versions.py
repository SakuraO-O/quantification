"""Create immutable dashboard versions from normalized Supabase facts."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .config import ASSETS, MARKET_TIMEZONE, STYLE_COMPASS_PAIRS
from .ingestion import CALCULATION_VERSION
from .style_compass import calculate_style_compass, style_recommendation_details
from .supabase_store import SupabaseStore


CURRENT_SIGNAL_FIELDS = (
    "close", "daily_return", "return_ytd", "return_1w", "return_1m", "return_1y", "return_3y",
    "ma20", "ma60", "ma120", "ma200", "ma20_slope_5d", "ma60_slope_10d",
    "ma120_slope_20d", "ma200_slope_40d", "short_trend", "mid_trend", "long_trend",
    "overall_status", "investment_advice", "pe", "pe_percentile", "pe_percentile_period",
    "valuation_status", "dividend_yield",
)


class DashboardPublisher:
    def __init__(self, store: SupabaseStore):
        self.store = store

    def _active_assets(self) -> list[dict]:
        return self.store.select("securities", filters={"is_active": "eq.true"}, order="asset_type.asc,name.asc")

    def _latest_signal(self, sid: str) -> dict | None:
        rows = self.store.select("asset_daily_signals", filters={"security_id": f"eq.{sid}"}, order="trade_date.desc", limit=1)
        return rows[0] if rows else None

    @staticmethod
    def _is_current_calculation(signal: dict | None) -> bool:
        """Only publish conclusions produced by the active calculation rules."""

        return bool(signal and signal.get("calculation_version") == CALCULATION_VERSION)

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
        """Read the database-owned allocation result; never recalculate it in Python."""

        return self.store.rpc("compute_portfolio_allocation")

    def _style_compass(self, assets_by_name: dict[str, dict]) -> list[dict]:
        result: list[dict] = []
        for left_name, right_name in STYLE_COMPASS_PAIRS:
            left_asset, right_asset = assets_by_name.get(left_name), assets_by_name.get(right_name)
            left_signal = self._latest_signal(left_asset["security_id"]) if left_asset else None
            right_signal = self._latest_signal(right_asset["security_id"]) if right_asset else None
            # A calculation-version migration may run before signal rebuild.
            # Do not let stale PE/advice values influence a new compass payload.
            left_signal = left_signal if self._is_current_calculation(left_signal) else None
            right_signal = right_signal if self._is_current_calculation(right_signal) else None
            left_history = pd.DataFrame(self.store.history(left_asset["security_id"])) if left_asset else pd.DataFrame()
            right_history = pd.DataFrame(self.store.history(right_asset["security_id"])) if right_asset else pd.DataFrame()
            left_history = left_history.rename(columns={"trade_date": "date"}).reindex(columns=["date", "close"])
            right_history = right_history.rename(columns={"trade_date": "date"}).reindex(columns=["date", "close"])
            compass = calculate_style_compass(left_history, right_history)
            if not left_history.empty and not right_history.empty:
                # The compass is only comparable through the latest date both assets can support.
                compass["as_of_date"] = min(
                    pd.Timestamp(left_history["date"].max()).date().isoformat(),
                    pd.Timestamp(right_history["date"].max()).date().isoformat(),
                )
            else:
                compass["as_of_date"] = None
            compass |= {
                "left": {"name": left_name, "pe_percentile": (left_signal or {}).get("pe_percentile"), "investment_advice": (left_signal or {}).get("investment_advice")},
                "right": {"name": right_name, "pe_percentile": (right_signal or {}).get("pe_percentile"), "investment_advice": (right_signal or {}).get("investment_advice")},
            }
            compass |= style_recommendation_details(
                str(compass["direction"]),
                (left_signal or {}).get("investment_advice"),
                (left_signal or {}).get("pe_percentile"),
                (right_signal or {}).get("investment_advice"),
                (right_signal or {}).get("pe_percentile"),
            )
            result.append(compass)
        return result

    @staticmethod
    def _unavailable_current_signal(expected_date, reason: str, last_valid_date: str | None = None) -> dict:
        """Keep a failed asset visible without presenting stale values as current."""
        return {
            "trade_date": expected_date.isoformat() if expected_date else None,
            "data_status": "delayed",
            "data_issue": reason,
            "last_valid_trade_date": last_valid_date,
        } | {field: None for field in CURRENT_SIGNAL_FIELDS}

    def publish(self, source_run_id: str | None = None) -> dict:
        assets = self._active_assets()
        records = []
        asset_issues = []
        valid_dates = []
        now = datetime.now(MARKET_TIMEZONE)
        expected_symbols = {asset["symbol"] for asset in ASSETS}
        actual_symbols = {asset["symbol"] for asset in assets}
        catalog_valid = actual_symbols == expected_symbols and len(assets) == len(ASSETS)
        if not catalog_valid:
            asset_issues.append(
                {
                    "reason": "资产清单不一致",
                    "missing": sorted(expected_symbols - actual_symbols),
                    "unexpected": sorted(actual_symbols - expected_symbols),
                }
            )
        for asset in assets:
            signal = self._latest_signal(asset["security_id"])
            expected_date = self.store.previous_trading_date(asset["market"], now.date())
            if not signal:
                issue = {"symbol": asset["symbol"], "reason": "缺少派生信号", "expected": expected_date.isoformat()}
                asset_issues.append(issue)
                signal = self._unavailable_current_signal(expected_date, issue["reason"])
            elif not self._is_current_calculation(signal):
                actual_date = pd.Timestamp(signal["trade_date"]).date()
                issue = {
                    "symbol": asset["symbol"], "reason": "信号版本待重算", "expected": expected_date.isoformat(),
                    "actual": actual_date.isoformat(), "calculation_version": signal.get("calculation_version"),
                }
                asset_issues.append(issue)
                signal = self._unavailable_current_signal(expected_date, issue["reason"], actual_date.isoformat())
            else:
                actual_date = pd.Timestamp(signal["trade_date"]).date()
                if actual_date < expected_date:
                    issue = {
                        "symbol": asset["symbol"], "reason": "行情日期滞后",
                        "expected": expected_date.isoformat(), "actual": actual_date.isoformat(),
                    }
                    asset_issues.append(issue)
                    signal = self._unavailable_current_signal(expected_date, issue["reason"], actual_date.isoformat())
                else:
                    valid_dates.append(actual_date.isoformat())
                    signal = signal | {
                        "data_status": "current",
                        "data_issue": None,
                        "last_valid_trade_date": actual_date.isoformat(),
                    }
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
        allocation = self._allocation()
        payload = {
            "schema_version": 2,
            "latest_market_date": max(valid_dates) if valid_dates else None,
            "assets": records,
            "allocation": allocation,
            "style_compass": self._style_compass(assets_by_name),
        }
        completeness = {
            "asset_issues": asset_issues,
            # Kept for consumers that already read the V2 field name.
            "missing_asset_signals": asset_issues,
            "asset_catalog_valid": catalog_valid,
            "allocation_configured": allocation is not None,
            "data_status": "degraded" if asset_issues else "current",
        }
        # A temporary source failure never blocks the other assets or their history.
        # Only an invalid asset catalogue is a release blocker.
        # Do not replace the API's last complete version while a rule-version
        # upgrade is waiting for its signal rebuild.  Other per-asset source
        # delays remain publishable by design.
        has_stale_calculation = any(issue.get("reason") == "信号版本待重算" for issue in asset_issues)
        is_complete = catalog_valid and not has_stale_calculation
        return self.store.publish_dashboard_version(
            payload, is_complete=is_complete, completeness=completeness, calculation_version=CALCULATION_VERSION, source_run_id=source_run_id
        )
