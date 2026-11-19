"""CLI entrypoint for the V2 data pipeline."""

from __future__ import annotations

import argparse
import os
from datetime import datetime

from .config import MARKET_TIMEZONE
from .assets import active_assets, provider_symbol_rows, security_rows
from .dashboard_versions import DashboardPublisher
from .dispatch import dispatch_morning_report
from .fundamental_sources import FundamentalSynchronizer
from .ingestion import MarketSynchronizer
from .supabase_store import SupabaseStore


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="趋势观察台数据管道。")
    parser.add_argument("command", choices=("begin-batch", "retry-morning-publication", "sync-security-metadata", "bootstrap", "sync-market", "sync-valuation", "sync-fundamentals", "rebuild-gem100", "rebuild-manual-index", "publish-dashboard", "dispatch-feishu"))
    parser.add_argument("--phase", choices=("morning", "evening", "manual"), default="manual")
    parser.add_argument("--symbol", choices=("sz399006", "980092", "hkHSTECH", "hkHSI"), help="rebuild-manual-index 时指定手动 PE 指数。")
    parser.add_argument("--market", choices=("CN", "HK", "US"), help="sync-market 时仅同步指定市场。")
    parser.add_argument("--force", action="store_true", help="忽略当日水位并进行重叠补抓。")
    parser.add_argument("--trigger", default="schedule", choices=("schedule", "manual", "retry"), help="任务触发方式。")
    return parser.parse_args(argv)


def require_store() -> SupabaseStore:
    store = SupabaseStore.from_env()
    if not store:
        raise RuntimeError("未配置 Supabase。请设置 SUPABASE_URL 与 SUPABASE_SECRET_KEY 后再运行 V2 任务。")
    return store


def run_bootstrap(store: SupabaseStore) -> None:
    store.seed_asset_master(security_rows(), provider_symbol_rows())
    print(f"已同步资产主数据：{len(security_rows())} 个启用资产。")


class PartialSyncError(RuntimeError):
    """A source failed for some assets; publication may use asset-level degradation."""


def run_market_sync(store: SupabaseStore, market: str | None, force: bool, trigger: str) -> None:
    run_bootstrap(store)
    assets = [asset for asset in active_assets() if not market or asset["market"] == market]
    synchronizer = MarketSynchronizer(store)
    results = synchronizer.sync_assets(assets, force=force, trigger_type=trigger)
    for item in results:
        print(f"{item.asset}｜{item.status}｜接收 {item.rows_received} 行｜变化 {item.rows_changed} 行｜{item.message}")
    if any(item.status == "failed" for item in results):
        raise PartialSyncError("部分资产同步失败，请查看 ingestion_runs 与 ingestion_watermarks。")


def main(argv=None):
    args = parse_args(argv)
    store = require_store()
    batch_id = os.getenv("DASHBOARD_BATCH_ID")
    task = f"{args.command}:{args.market or 'all'}"
    tracked = batch_id and args.command in ("sync-market", "sync-valuation", "sync-fundamentals")
    if tracked:
        store.record_batch_task(batch_id, task, "running")
    try:
        if args.command == "begin-batch":
            tasks_by_phase = {
                "morning": ["sync-market:CN", "sync-market:HK", "sync-market:US", "sync-valuation:HK", "sync-valuation:US", "sync-fundamentals:all"],
                "evening": ["sync-market:CN", "sync-valuation:CN"],
                "manual": [],
            }
            names = tasks_by_phase[args.phase]
            print(store.create_dashboard_batch(args.phase, dict.fromkeys(names, "running")))
        elif args.command == "retry-morning-publication":
            day = datetime.now(MARKET_TIMEZONE).date().isoformat()
            batches = store.select("dashboard_batches", filters={"phase": "eq.morning", "business_date": f"eq.{day}"}, order="started_at.desc", limit=1)
            if batches:
                DashboardPublisher(store).publish(batch_id=batches[0]["batch_id"])
        elif args.command == "bootstrap":
            run_bootstrap(store)
        elif args.command == "sync-market":
            run_market_sync(store, args.market, args.force, args.trigger)
        elif args.command == "sync-valuation":
            run_bootstrap(store)
            assets = [asset for asset in active_assets() if not args.market or asset["market"] == args.market]
            results = MarketSynchronizer(store).sync_valuations(assets, force=args.force, trigger_type=args.trigger)
            for item in results:
                print(f"估值 {item.asset}｜{item.status}｜接收 {item.rows_received} 行｜变化 {item.rows_changed} 行｜{item.message}")
            if any(item.status == "failed" for item in results):
                raise PartialSyncError("部分估值同步失败，请检查 ingestion_run_items 与 ingestion_watermarks。")
        elif args.command == "sync-security-metadata":
            run_bootstrap(store)
            results = FundamentalSynchronizer(store).sync_listing_dates(force=args.force)
            for item in results:
                print(f"上市信息 {item.asset}｜{item.status}｜{item.message}")
            if any(item.status == "failed" for item in results):
                raise PartialSyncError("部分上市日期未取得，保留现有年份范围")
        elif args.command == "sync-fundamentals":
            run_bootstrap(store)
            results = FundamentalSynchronizer(store).sync_assets(active_assets(), force=args.force, trigger_type=args.trigger)
            for item in results:
                print(f"基本面 {item.asset}｜{item.status}｜接收 {item.rows_received} 项｜变化 {item.rows_changed} 项｜{item.message}")
            if any(item.status == "failed" for item in results):
                raise PartialSyncError("部分股票基本面同步失败，请检查 ingestion_run_items 与 data_quality_issues。")
        elif args.command == "rebuild-gem100":
            asset = next(asset for asset in active_assets() if asset["symbol"] == "sz399006")
            count = MarketSynchronizer(store).recalculate_valuation_signals(asset, None)
            version = DashboardPublisher(store).publish(batch_id=batch_id)
            print(f"已重算创业板100的 {count} 行信号并发布看板版本：{version['dashboard_version_id']}")
        elif args.command == "rebuild-manual-index":
            if not args.symbol:
                raise RuntimeError("rebuild-manual-index 需要 --symbol。")
            asset = next(asset for asset in active_assets() if asset["symbol"] == args.symbol)
            count = MarketSynchronizer(store).recalculate_valuation_signals(asset, None)
            version = DashboardPublisher(store).publish(batch_id=batch_id)
            print(f"已重算{asset['name']}的 {count} 行信号并发布看板版本：{version['dashboard_version_id']}")
        elif args.command == "publish-dashboard":
            version = DashboardPublisher(store).publish(batch_id=batch_id)
            print(f"已发布看板版本：{version['dashboard_version_id']}")
        elif args.command == "dispatch-feishu":
            result = dispatch_morning_report(store)
            print(result.message)
        if tracked:
            store.record_batch_task(batch_id, task, "succeeded")
    except PartialSyncError:
        if tracked:
            store.record_batch_task(batch_id, task, "degraded")
        raise
    except Exception:
        if tracked:
            store.record_batch_task(batch_id, task, "failed")
        raise
    except KeyboardInterrupt:
        print("\n已取消趋势观察。", flush=True)


if __name__ == "__main__":
    main()
