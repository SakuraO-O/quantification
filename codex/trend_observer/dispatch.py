"""Version-deduplicated, Monday-to-Saturday Feishu morning dispatch."""

from __future__ import annotations

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
    version = store.latest_dashboard_version()
    if version and version.get("is_complete"):
        key = f"morning_report:{version['dashboard_version_id']}"
        if store.has_dispatch_key(key):
            return DispatchResult("skipped", "该数据版本已发送")
        try:
            status_code = send_text_message(format_dashboard_version_message(version))
            store.record_dispatch(version["dashboard_version_id"], "morning_report", "succeeded", dispatch_key=key, response_code=status_code)
            return DispatchResult("succeeded", "飞书晨报已发送")
        except Exception as exc:
            store.record_dispatch(version["dashboard_version_id"], "morning_report", "failed", dispatch_key=key, error=str(exc))
            raise
    if now.time() < DELAY_DEADLINE:
        return DispatchResult("waiting", "等待完整数据版本，09:30前将继续每10分钟检查")
    key = f"delay_notice:{now:%Y-%m-%d}"
    if store.has_dispatch_key(key):
        return DispatchResult("skipped", "当日延迟提示已发送")
    try:
        missing = (version or {}).get("completeness", {}).get("missing_asset_signals", ["必要行情数据集"])
        missing_labels = [item.get("symbol", str(item)) if isinstance(item, dict) else str(item) for item in missing]
        status_code = send_text_message(format_delay_message(missing_labels, now))
        store.record_dispatch(None, "delay_notice", "succeeded", dispatch_key=key, response_code=status_code)
        return DispatchResult("delayed", "数据延迟提示已发送")
    except Exception as exc:
        store.record_dispatch(None, "delay_notice", "failed", dispatch_key=key, error=str(exc))
        raise
