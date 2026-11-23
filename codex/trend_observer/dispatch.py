"""Business-date-deduplicated, Monday-to-Saturday Feishu morning dispatch."""

from __future__ import annotations

import requests

from dataclasses import dataclass
from datetime import datetime, time

from .config import MARKET_TIMEZONE
from .feishu import format_dashboard_version_message, format_delay_message, send_text_message
from .supabase_store import SupabaseStore


TARGET_TIME = time(8, 0)
DELAY_DEADLINE = time(9, 30)


@dataclass(frozen=True)
class DispatchResult:
    status: str
    message: str


def dispatch_morning_report(store: SupabaseStore, now: datetime | None = None) -> DispatchResult:
    now = now.astimezone(MARKET_TIMEZONE) if now else datetime.now(MARKET_TIMEZONE)
    if now.weekday() == 6:
        return DispatchResult("skipped", "周日不推送")
    if now.time() < TARGET_TIME:
        return DispatchResult("skipped", "未到08:00发送窗口")
    day = now.date().isoformat()
    version = store.ready_morning_version(day)
    if version and hasattr(store, "review_status"):
        if store.review_status(version["dashboard_version_id"]).get("invalidations"):
            version = None
    if version:
        key = f"morning_report:{day}"
        if not store.claim_dispatch(key, version["dashboard_version_id"], "morning_report"):
            return DispatchResult("skipped", "当日晨报已发送或正在发送")
        try:
            status_code = send_text_message(format_dashboard_version_message(version))
        except (requests.Timeout, requests.ConnectionError, ValueError) as exc:
            raise RuntimeError("晨报发送结果未知，保留发送占用，请核查飞书回执") from exc
        except Exception as exc:
            store.record_dispatch(version["dashboard_version_id"], "morning_report", "failed", dispatch_key=key, error=str(exc))
            raise
        store.record_dispatch(version["dashboard_version_id"], "morning_report", "succeeded", dispatch_key=key, response_code=status_code)
        return DispatchResult("succeeded", "飞书晨报已发送")
    if now.time() < DELAY_DEADLINE:
        return DispatchResult("waiting", "等待完整数据版本，09:30前将继续每10分钟检查")
    key = f"delay_notice:{now:%Y-%m-%d}"
    if not store.claim_dispatch(key, None, "delay_notice"):
        return DispatchResult("skipped", "当日延迟提示已发送或正在发送")
    try:
        latest = store.latest_dashboard_version()
        missing = (latest or {}).get("completeness", {}).get("missing_asset_signals", ["当日早间同步／发布就绪检查"])
        missing_labels = [item.get("symbol", str(item)) if isinstance(item, dict) else str(item) for item in missing]
        runtime = store.dashboard_runtime_status()
        batch = runtime.get("latest_batch") or {}
        task_labels = {
            "sync-market:CN":"境内行情", "sync-market:HK":"港股行情", "sync-market:US":"美股行情",
            "sync-valuation:HK":"港股估值", "sync-valuation:US":"美股估值",
            "sync-fundamentals:all":"财报与分红",
        }
        if batch.get("phase") == "morning" and batch.get("business_date") == day:
            missing_labels.extend(task_labels.get(task, "上游数据同步") for task, status in batch.get("tasks", {}).items()
                                  if status not in ("succeeded", "degraded"))
        if (runtime.get("latest_attempt") or {}).get("status") in ("running", "failed"):
            missing_labels.append("看板计算与发布")
        missing_labels = list(dict.fromkeys(missing_labels)) or ["当日早间就绪检查"]
        status_code = send_text_message(format_delay_message(missing_labels, now))
    except (requests.Timeout, requests.ConnectionError, ValueError) as exc:
        raise RuntimeError("延迟提示发送结果未知，保留发送占用，请核查飞书回执") from exc
    except Exception as exc:
        store.record_dispatch(None, "delay_notice", "failed", dispatch_key=key, error=str(exc))
        raise
    store.record_dispatch(None, "delay_notice", "succeeded", dispatch_key=key, response_code=status_code)
    return DispatchResult("delayed", "数据延迟提示已发送")
