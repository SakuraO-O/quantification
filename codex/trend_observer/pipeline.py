"""Single-pass fetch and analysis pipeline."""

from copy import deepcopy

import numpy as np
import pandas as pd

from .analysis import enrich_history
from .config import ASSETS, MIN_HISTORY_ROWS, REPORT_COLUMNS
from .data_sources import fetch_history, make_session
from .dividends import apply_dividend_config
from .models import round_output


def empty_result(asset, error):
    return {column: np.nan for column in REPORT_COLUMNS} | {
        "name": asset["name"],
        "symbol": asset["symbol"],
        "market": asset["market"],
        "asset_type": asset["asset_type"],
        "date": "",
        "short_trend": "数据不足",
        "mid_trend": "数据不足",
        "long_trend": "数据不足",
        "overall_status": "数据不足",
        "signal_tags": f"数据不足, {error}",
        "valuation_status": "估值数据缺失" if asset["asset_type"] == "指数" else "",
        "error": error,
    }


def latest_result(asset, history):
    if len(history) < MIN_HISTORY_ROWS:
        return empty_result(asset, f"仅获取到 {len(history)} 个交易日，至少需要 {MIN_HISTORY_ROWS} 个交易日。")
    current = history.iloc[-1]
    if current["overall_status"] in ("", "数据预热"):
        return empty_result(asset, "均线或斜率计算所需历史数据不足。")
    result = {column: current.get(column, np.nan) for column in REPORT_COLUMNS}
    result.update({"error": ""})
    return result


def build_results(assets=None):
    configured_assets = apply_dividend_config(deepcopy(assets or ASSETS))
    latest_rows = []
    history_frames = []
    with make_session() as session:
        for number, asset in enumerate(configured_assets, start=1):
            print(f"[{number:02d}/{len(configured_assets)}] 正在分析 {asset['name']} ({asset['symbol']})...", flush=True)
            try:
                raw_history = fetch_history(session, asset)
                history = enrich_history(raw_history, asset)
                history_frames.append(history)
                latest_rows.append(latest_result(asset, history))
            except Exception as exc:
                latest_rows.append(empty_result(asset, str(exc)))
                print(f"  获取失败: {exc}", flush=True)

    results = round_output(pd.DataFrame(latest_rows)[REPORT_COLUMNS])
    history_output = pd.concat(history_frames, ignore_index=True) if history_frames else pd.DataFrame()
    return results, history_output

