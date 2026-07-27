"""Small Supabase REST repository used by ingestion and notification jobs.

Keeping the HTTP boundary here avoids coupling market calculations to a client
SDK and makes unit tests possible with a fake session.  A service-role key is
only read in CI/Edge environments; it is never returned by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from typing import Any

import requests


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def payload_hash(payload: Any) -> str:
    serialized = json.dumps(_json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    service_key: str

    @classmethod
    def from_env(cls) -> "SupabaseSettings | None":
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        service_key = (os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not url and not service_key:
            return None
        if not url or not service_key:
            raise RuntimeError("SUPABASE_URL 与 SUPABASE_SECRET_KEY 必须同时配置。")
        return cls(url=url, service_key=service_key)


class SupabaseStore:
    """Repository for the V2 facts, watermarks and published dashboard versions."""

    def __init__(self, settings: SupabaseSettings, session: requests.Session | None = None, timeout: tuple[int, int] = (5, 20)):
        self.settings = settings
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "SupabaseStore | None":
        settings = SupabaseSettings.from_env()
        return cls(settings) if settings else None

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "apikey": self.settings.service_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # New sb_secret_* keys are opaque API keys, not JWTs.  The gateway
        # derives service-role authorization from the apikey header.  Legacy
        # service_role JWTs still need the Bearer header for compatibility.
        if not self.settings.service_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.settings.service_key}"
        return headers

    def _request(self, method: str, path: str, *, params: dict | None = None, data: Any = None, headers: dict | None = None) -> Any:
        merged_headers = self.headers | (headers or {})
        response = self.session.request(
            method,
            f"{self.settings.url}{path}",
            params=params,
            json=_json_value(data) if data is not None else None,
            headers=merged_headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def upsert(self, table: str, rows: list[dict] | dict, on_conflict: str) -> list[dict]:
        payload = rows if isinstance(rows, list) else [rows]
        if not payload:
            return []
        return self._request(
            "POST",
            f"/rest/v1/{table}",
            params={"on_conflict": on_conflict},
            data=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        ) or []

    def insert(self, table: str, rows: list[dict] | dict) -> list[dict]:
        """Insert rows without an ``on_conflict`` clause.

        Used for immutable event records when a PostgREST schema cache cannot
        resolve a newly created composite unique constraint.  Callers must
        query and exclude existing identities first.
        """

        payload = rows if isinstance(rows, list) else [rows]
        if not payload:
            return []
        return self._request(
            "POST", f"/rest/v1/{table}", data=payload,
            headers={"Prefer": "return=representation"},
        ) or []

    def select(self, table: str, *, select: str = "*", filters: dict[str, str] | None = None, order: str | None = None, limit: int | None = None) -> list[dict]:
        params: dict[str, str] = {"select": select}
        params.update(filters or {})
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)
        if limit is not None:
            return self._request("GET", f"/rest/v1/{table}", params=params) or []
        rows: list[dict] = []
        page_size = 1000
        while True:
            page = self._request(
                "GET",
                f"/rest/v1/{table}",
                params=params,
                headers={"Range-Unit": "items", "Range": f"{len(rows)}-{len(rows) + page_size - 1}"},
            ) or []
            rows.extend(page)
            if len(page) < page_size:
                return rows

    def patch(self, table: str, filters: dict[str, str], row: dict) -> list[dict]:
        return self._request(
            "PATCH",
            f"/rest/v1/{table}",
            params=filters,
            data=row,
            headers={"Prefer": "return=representation"},
        ) or []

    def rpc(self, function: str, params: dict | None = None) -> Any:
        """Call a database-owned calculation through PostgREST RPC."""

        return self._request("POST", f"/rest/v1/rpc/{function}", data=params or {})

    def seed_asset_master(self, securities: list[dict], mappings: list[dict]) -> None:
        self.upsert("securities", securities, "security_id")
        self.upsert("provider_symbol_map", mappings, "security_id,provider")

    def get_watermark(self, dataset_key: str) -> dict | None:
        rows = self.select("ingestion_watermarks", filters={"dataset_key": f"eq.{dataset_key}"}, limit=1)
        return rows[0] if rows else None

    def save_watermark(self, row: dict) -> None:
        self.upsert("ingestion_watermarks", row | {"updated_at": datetime.now(timezone.utc).isoformat()}, "dataset_key")

    def start_run(self, job_type: str, trigger_type: str) -> str:
        rows = self._request(
            "POST",
            "/rest/v1/ingestion_runs",
            data={"job_type": job_type, "trigger_type": trigger_type},
            headers={"Prefer": "return=representation"},
        )
        return rows[0]["ingestion_run_id"]

    def finish_run(self, run_id: str, status: str, summary: dict | None = None, error: str | None = None) -> None:
        self.patch(
            "ingestion_runs",
            {"ingestion_run_id": f"eq.{run_id}"},
            {"status": status, "summary": summary or {}, "error": error, "finished_at": datetime.now(timezone.utc).isoformat()},
        )

    def add_run_item(self, run_id: str, **item: Any) -> None:
        self._request("POST", "/rest/v1/ingestion_run_items", data={"ingestion_run_id": run_id} | item)

    def record_quality_issue(self, dataset_key: str, severity: str, issue_type: str, details: dict) -> None:
        self._request(
            "POST",
            "/rest/v1/data_quality_issues",
            data={"dataset_key": dataset_key, "severity": severity, "issue_type": issue_type, "details": details},
        )

    def save_ingestion_source_record(self, row: dict) -> str:
        """Save compact provenance metadata, never a source response body."""

        identity = {
            "dataset_key": f"eq.{row['dataset_key']}",
            "source": f"eq.{row['source']}",
            "source_record_id": f"eq.{row['source_record_id']}",
            "content_hash": f"eq.{row['content_hash']}",
        }
        existing = self.select(
            "ingestion_source_records", select="ingestion_source_record_id", filters=identity, limit=1
        )
        if existing:
            return existing[0]["ingestion_source_record_id"]
        rows = self._request(
            "POST", "/rest/v1/ingestion_source_records", data=row,
            headers={"Prefer": "return=representation"},
        )
        return rows[0]["ingestion_source_record_id"]

    def history(self, security_id: str, start_date: str | None = None) -> list[dict]:
        filters = {"security_id": f"eq.{security_id}"}
        if start_date:
            filters["trade_date"] = f"gte.{start_date}"
        return self.select("market_daily", filters=filters, order="trade_date.asc")

    def latest_market_date(self, security_id: str) -> str | None:
        rows = self.select(
            "market_daily", select="trade_date", filters={"security_id": f"eq.{security_id}"}, order="trade_date.desc", limit=1
        )
        return rows[0]["trade_date"] if rows else None

    def latest_signal_state(self, security_id: str) -> dict | None:
        rows = self.select(
            "asset_daily_signals",
            select="trade_date,calculation_version",
            filters={"security_id": f"eq.{security_id}"},
            order="trade_date.desc",
            limit=1,
        )
        return rows[0] if rows else None

    def calendar_is_trading_day(self, market: str, value: date) -> bool | None:
        rows = self.select(
            "market_calendars",
            select="is_trading_day",
            filters={"market": f"eq.{market}", "trade_date": f"eq.{value.isoformat()}"},
            limit=1,
        )
        return bool(rows[0]["is_trading_day"]) if rows else None

    def previous_trading_date(self, market: str, before_date: date) -> date:
        rows = self.select(
            "market_calendars",
            select="trade_date",
            filters={"market": f"eq.{market}", "is_trading_day": "eq.true", "trade_date": f"lt.{before_date.isoformat()}"},
            order="trade_date.desc",
            limit=1,
        )
        if rows:
            return date.fromisoformat(rows[0]["trade_date"])
        # Safe bootstrap fallback until the official calendar has been loaded.
        candidate = before_date
        while True:
            candidate = candidate.fromordinal(candidate.toordinal() - 1)
            if candidate.weekday() < 5:
                return candidate

    def valuation_history(self, security_id: str, valuation_type: str = "pe") -> list[dict]:
        return self.select(
            "valuation_daily",
            select="trade_date,value,source,methodology",
            filters={"security_id": f"eq.{security_id}", "valuation_type": f"eq.{valuation_type}"},
            order="trade_date.asc",
        )

    def save_market_rows(self, rows: list[dict]) -> list[dict]:
        updated_at = datetime.now(timezone.utc).isoformat()
        return self.upsert("market_daily", [row | {"updated_at": updated_at} for row in rows], "security_id,trade_date")

    def save_valuation_rows(self, rows: list[dict]) -> list[dict]:
        updated_at = datetime.now(timezone.utc).isoformat()
        return self.upsert("valuation_daily", [row | {"updated_at": updated_at} for row in rows], "security_id,trade_date,valuation_type")

    def save_signal_rows(self, rows: list[dict]) -> list[dict]:
        updated_at = datetime.now(timezone.utc).isoformat()
        return self.upsert("asset_daily_signals", [row | {"updated_at": updated_at} for row in rows], "security_id,trade_date")

    def save_style_result(self, row: dict) -> list[dict]:
        return self.upsert("style_compass_results", row, "as_of_date,left_security_id,right_security_id,calculation_version")

    def publish_dashboard_version(self, payload: dict, *, is_complete: bool, completeness: dict, calculation_version: str, source_run_id: str | None = None) -> dict:
        content_hash = payload_hash(payload | {"is_complete": is_complete, "completeness": completeness, "calculation_version": calculation_version})
        rows = self.upsert(
            "dashboard_versions",
            {
                "latest_market_date": payload.get("latest_market_date"),
                "is_complete": is_complete,
                "completeness": completeness,
                "payload": payload,
                "calculation_version": calculation_version,
                "source_run_id": source_run_id,
                "content_hash": content_hash,
            },
            "content_hash",
        )
        return rows[0]

    def latest_complete_dashboard_version(self) -> dict | None:
        rows = self.select("dashboard_versions", filters={"is_complete": "eq.true"}, order="generated_at.desc", limit=1)
        return rows[0] if rows else None

    def latest_dashboard_version(self) -> dict | None:
        rows = self.select("dashboard_versions", order="generated_at.desc", limit=1)
        return rows[0] if rows else None

    def has_dispatch(self, version_id: str, message_type: str) -> bool:
        rows = self.select(
            "notification_dispatches",
            select="notification_dispatch_id",
            filters={"dashboard_version_id": f"eq.{version_id}", "message_type": f"eq.{message_type}", "status": "eq.succeeded"},
            limit=1,
        )
        return bool(rows)

    def has_dispatch_key(self, dispatch_key: str) -> bool:
        rows = self.select("notification_dispatches", select="notification_dispatch_id", filters={"dispatch_key": f"eq.{dispatch_key}", "status": "eq.succeeded"}, limit=1)
        return bool(rows)

    def record_dispatch(self, version_id: str | None, message_type: str, status: str, *, dispatch_key: str, response_code: int | None = None, error: str | None = None) -> None:
        self.upsert(
            "notification_dispatches",
            {
                "dashboard_version_id": version_id,
                "message_type": message_type,
                "status": status,
                "response_code": response_code,
                "error": error,
                "dispatch_key": dispatch_key,
            },
            "dispatch_key",
        )
