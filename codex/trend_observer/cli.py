"""CLI entrypoint for the V2 data pipeline and temporary V1-compatible export."""

from __future__ import annotations

import argparse

from .assets import active_assets, provider_symbol_rows, security_rows
from .config import (
    CSV_OUTPUT,
    DASHBOARD_OUTPUT,
    HISTORY_CSV_OUTPUT,
    HISTORY_DIR,
    HISTORY_JSON_OUTPUT,
    MARKET_VALUATION_OUTPUT,
    MARKDOWN_OUTPUT,
    SNAPSHOT_OUTPUT,
)
from .dashboard_versions import DashboardPublisher
from .dispatch import dispatch_morning_report
from .feishu import post_feishu_message
from .ingestion import MarketSynchronizer
from .market_valuation import run_market_valuation
from .outputs import export_all, format_markdown
from .pipeline import build_results
from .supabase_store import SupabaseStore


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="趋势观察台数据管道。")
    parser.add_argument("command", nargs="?", default="legacy-export", choices=("legacy-export", "bootstrap", "sync-market", "publish-dashboard", "dispatch-feishu"))
    parser.add_argument("--notify", action="store_true", help="仅兼容旧 JSON 流程：导出后发送飞书。")
    parser.add_argument("--market", choices=("CN", "HK", "US"), help="sync-market 时仅同步指定市场。")
    parser.add_argument("--force", action="store_true", help="忽略当日水位并进行重叠补抓。")
    parser.add_argument("--trigger", default="schedule", choices=("schedule", "manual", "retry"), help="任务触发方式。")
    return parser.parse_args(argv)


def run_legacy_export(notify=False):
    """Migration-period compatibility output; it is not the V2 fact source."""

    results, history_output = build_results()
    snapshot = export_all(results, history_output)
    run_market_valuation()
    print(f"\n已导出 CSV: {CSV_OUTPUT}")
    print(f"已导出 Markdown: {MARKDOWN_OUTPUT}")
    print(f"已导出统一快照 JSON: {SNAPSHOT_OUTPUT}")
    print(f"已导出网页数据: {DASHBOARD_OUTPUT}")
    print(f"已导出历史 JSON: {HISTORY_JSON_OUTPUT}")
    print(f"已导出拆分历史目录: {HISTORY_DIR}")
    print(f"已导出历史 CSV: {HISTORY_CSV_OUTPUT}")
    print(f"已导出市场估值快照: {MARKET_VALUATION_OUTPUT}\n")
    print(format_markdown(results))
    if notify:
        post_feishu_message(snapshot)


def require_store() -> SupabaseStore:
    store = SupabaseStore.from_env()
    if not store:
        raise RuntimeError("未配置 Supabase。请设置 SUPABASE_URL 与 SUPABASE_SECRET_KEY 后再运行 V2 任务。")
    return store


def run_bootstrap(store: SupabaseStore) -> None:
    store.seed_asset_master(security_rows(), provider_symbol_rows())
    print(f"已同步资产主数据：{len(security_rows())} 个启用资产。")


def run_market_sync(store: SupabaseStore, market: str | None, force: bool, trigger: str) -> None:
    run_bootstrap(store)
    assets = [asset for asset in active_assets() if not market or asset["market"] == market]
    results = MarketSynchronizer(store).sync_assets(assets, force=force, trigger_type=trigger)
    for item in results:
        print(f"{item.asset}｜{item.status}｜接收 {item.rows_received} 行｜变化 {item.rows_changed} 行｜{item.message}")
    if any(item.status == "failed" for item in results):
        raise RuntimeError("部分资产同步失败，请查看 ingestion_runs 与 ingestion_watermarks。")


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "legacy-export":
            run_legacy_export(args.notify)
        else:
            store = require_store()
            if args.command == "bootstrap":
                run_bootstrap(store)
            elif args.command == "sync-market":
                run_market_sync(store, args.market, args.force, args.trigger)
            elif args.command == "publish-dashboard":
                version = DashboardPublisher(store).publish()
                print(f"已发布看板版本：{version['dashboard_version_id']}")
            elif args.command == "dispatch-feishu":
                result = dispatch_morning_report(store)
                print(result.message)
    except KeyboardInterrupt:
        print("\n已取消趋势观察。", flush=True)


if __name__ == "__main__":
    main()
