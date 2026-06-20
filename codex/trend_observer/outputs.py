"""Report, dashboard, history, and snapshot outputs."""

import json
import shutil
from datetime import datetime

import pandas as pd

from .config import (
    CSV_OUTPUT,
    DASHBOARD_OUTPUT,
    DATA_DIR,
    DISCLAIMER,
    HISTORY_CSV_OUTPUT,
    HISTORY_DIR,
    HISTORY_JSON_OUTPUT,
    HISTORY_MANIFEST_OUTPUT,
    MARKET_TIMEZONE,
    MARKDOWN_OUTPUT,
    REPORT_COLUMNS,
    SNAPSHOT_OUTPUT,
    SNAPSHOT_SCHEMA_VERSION,
)
from .models import clean_value, round_output


def generated_at_now():
    return datetime.now(MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def records_from_results(results):
    records = []
    for row in results.to_dict("records"):
        signals = [tag.strip() for tag in str(row["signal_tags"]).split(",") if tag.strip()]
        records.append({key: clean_value(value) for key, value in row.items()} | {"signals": signals})
    return records


def format_markdown(results):
    table = results.copy()
    for column in ["close", "MA20", "MA60", "MA120", "MA200", "last_year_dividend", "dividend_yield", "pe", "pe_percentile"]:
        table[column] = table[column].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    for column in [
        "daily_return",
        "return_ytd",
        "return_1w",
        "return_1m",
        "return_1y",
        "return_3y",
        "ma20_slope_5d",
        "ma60_slope_10d",
        "ma120_slope_20d",
        "ma200_slope_40d",
    ]:
        table[column] = table[column].map(lambda value: "" if pd.isna(value) else f"{value:.2f}%")
    return (
        "# 三周期均线趋势观察报告\n\n"
        f"生成时间：{datetime.now(MARKET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}（Asia/Shanghai）\n\n"
        + table[REPORT_COLUMNS].to_markdown(index=False)
        + "\n\n"
        + DISCLAIMER
        + "\n"
    )


def export_dashboard_data(results, generated_at):
    DASHBOARD_OUTPUT.write_text(
        json.dumps({"generated_at": generated_at, "assets": records_from_results(results)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def export_history_data(history, generated_at):
    output = round_output(history)
    output.to_csv(HISTORY_CSV_OUTPUT, index=False, encoding="utf-8-sig")
    records = [{key: clean_value(value) for key, value in row.items()} for row in output.to_dict("records")]
    HISTORY_JSON_OUTPUT.write_text(
        json.dumps({"generated_at": generated_at, "history": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if HISTORY_DIR.exists():
        shutil.rmtree(HISTORY_DIR)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    manifest_assets = []
    if records:
        for symbol, group in output.groupby("symbol", sort=False):
            asset_records = [{key: clean_value(value) for key, value in row.items()} for row in group.to_dict("records")]
            file_name = f"{symbol}.json"
            (HISTORY_DIR / file_name).write_text(
                json.dumps({"generated_at": generated_at, "symbol": symbol, "history": asset_records}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            first = group.iloc[0]
            manifest_assets.append(
                {
                    "name": clean_value(first.get("name")),
                    "symbol": clean_value(symbol),
                    "asset_type": clean_value(first.get("asset_type")),
                    "path": file_name,
                    "rows": int(len(group)),
                    "start_date": clean_value(group.iloc[0].get("date")),
                    "end_date": clean_value(group.iloc[-1].get("date")),
                }
            )
    HISTORY_MANIFEST_OUTPUT.write_text(
        json.dumps({"generated_at": generated_at, "assets": manifest_assets}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_assets


def export_snapshot(results, manifest_assets, generated_at):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "assets": records_from_results(results),
        "history_manifest": {"generated_at": generated_at, "assets": manifest_assets},
    }
    SNAPSHOT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_snapshot(path=SNAPSHOT_OUTPUT):
    return json.loads(path.read_text(encoding="utf-8"))


def export_all(results, history):
    generated_at = generated_at_now()
    results.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")
    MARKDOWN_OUTPUT.write_text(format_markdown(results), encoding="utf-8")
    export_dashboard_data(results, generated_at)
    manifest_assets = export_history_data(history, generated_at)
    snapshot = export_snapshot(results, manifest_assets, generated_at)
    return snapshot

