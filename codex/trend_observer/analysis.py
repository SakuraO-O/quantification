"""Indicator, trend, valuation, and signal calculations."""

import numpy as np
import pandas as pd

from .config import PE_MIN_PERIODS, PE_WINDOW_ROWS
from .dividends import calculate_dividend_yield


def percentile_last(values):
    clean = values[~np.isnan(values)]
    if len(clean) < PE_MIN_PERIODS:
        return np.nan
    return float((clean <= clean[-1]).mean() * 100)


def add_indicators(frame):
    data = frame.copy()
    data["daily_return"] = data["close"] / data["close"].shift(1) - 1
    year_start_close = data.groupby(data["date"].dt.year)["close"].transform("first")
    data["return_ytd"] = data["close"] / year_start_close - 1
    data["return_1w"] = data["close"] / data["close"].shift(5) - 1
    data["return_1m"] = data["close"] / data["close"].shift(21) - 1
    data["return_1y"] = data["close"] / data["close"].shift(252) - 1
    data["return_3y"] = data["close"] / data["close"].shift(756) - 1
    for period in [20, 60, 120, 200]:
        data[f"MA{period}"] = data["close"].rolling(period).mean()
    data["ma20_slope_5d"] = data["MA20"] / data["MA20"].shift(5) - 1
    data["ma60_slope_10d"] = data["MA60"] / data["MA60"].shift(10) - 1
    data["ma120_slope_20d"] = data["MA120"] / data["MA120"].shift(20) - 1
    data["ma200_slope_40d"] = data["MA200"] / data["MA200"].shift(40) - 1
    if "pe_percentile_override" in data:
        data["pe_percentile"] = data["pe_percentile_override"]
    elif data["pe"].notna().sum() >= PE_MIN_PERIODS:
        data["pe_percentile"] = data["pe"].rolling(PE_WINDOW_ROWS, min_periods=PE_MIN_PERIODS).apply(percentile_last, raw=True)
    else:
        data["pe_percentile"] = np.nan
    return data


def slope_direction(value, threshold):
    if pd.isna(value):
        return ""
    if value > threshold:
        return "向上"
    if value < -threshold:
        return "向下"
    return "走平"


def determine_short_trend(row):
    if row["close"] > row["MA20"] and row["ma20_slope_5d"] > 0.01:
        return "短期强势"
    if row["close"] < row["MA20"] and row["ma20_slope_5d"] < -0.01:
        return "短期转弱"
    return "短期震荡"


def determine_mid_trend(row):
    if row["close"] < row["MA120"] and row["MA60"] < row["MA120"] and row["ma120_slope_20d"] < -0.005:
        return "中期下跌"
    if row["close"] > row["MA120"] and row["MA60"] > row["MA120"] and row["ma120_slope_20d"] > 0.005:
        return "中期上升"
    if row["close"] > row["MA120"] and (row["MA60"] <= row["MA120"] or abs(row["ma120_slope_20d"]) <= 0.005):
        return "中期修复"
    if row["close"] < row["MA120"] and row["ma120_slope_20d"] <= 0.005:
        return "中期转弱"
    return "中期修复"


def determine_long_trend(row):
    if row["close"] < row["MA200"] and row["MA120"] < row["MA200"] and row["ma200_slope_40d"] < -0.005:
        return "长期下跌"
    if row["close"] > row["MA200"] and row["MA120"] > row["MA200"] and row["ma200_slope_40d"] > 0.005:
        return "长期上升"
    if row["close"] > row["MA200"] and (row["MA120"] <= row["MA200"] or abs(row["ma200_slope_40d"]) <= 0.005):
        return "长期修复"
    if row["close"] < row["MA200"] and row["ma200_slope_40d"] >= -0.005:
        return "长期转弱"
    return "长期修复"


def determine_overall_status(row):
    if row["mid_trend"] == "中期下跌" and row["long_trend"] == "长期下跌":
        return "下跌通道"
    if row["short_trend"] == "短期强势" and row["mid_trend"] == "中期上升" and row["long_trend"] == "长期上升":
        return "强趋势"
    if row["mid_trend"] == "中期上升" and row["long_trend"] == "长期上升":
        return "健康上升"
    if row["mid_trend"] == "中期上升" and row["long_trend"] != "长期上升":
        return "趋势分歧"
    if row["long_trend"] == "长期修复" or row["mid_trend"] == "中期修复":
        return "趋势修复"
    if row["mid_trend"] == "中期转弱" or row["long_trend"] == "长期转弱":
        return "趋势转弱"
    return "趋势修复"


def valuation_status(value):
    if pd.isna(value):
        return "估值数据缺失"
    if value < 15:
        return "极低估"
    if value < 35:
        return "低估"
    if value < 70:
        return "合理"
    if value < 90:
        return "偏高"
    return "高估"


def determine_index_investment_advice(row):
    """Return the index-only action label defined by the dashboard PRD.

    This deliberately ignores portfolio amounts and style strength.  Those are
    separate layers and must not turn a trend/valuation observation into a
    transaction instruction.
    """

    if row.get("long_trend") == "长期下跌":
        return "暂停参与"
    if row.get("long_trend") in {None, "", "数据不足"} or row.get("mid_trend") in {None, "", "数据不足"}:
        return "数据不足"
    percentile = row.get("pe_percentile")
    if pd.notna(percentile) and percentile >= 90:
        return "仅持有"
    if row["long_trend"] == "长期上升" and row["mid_trend"] == "中期上升" and pd.notna(percentile) and percentile < 35:
        return "优先新增"
    if (
        row["long_trend"] == "长期上升"
        and row["mid_trend"] in {"中期上升", "中期修复"}
        and pd.notna(percentile)
        and 35 <= percentile < 60
    ):
        return "可新增"
    if row["long_trend"] == "长期上升":
        return "仅持有"
    return "观察等待"


def determine_stock_trend_advice(row):
    if row.get("long_trend") == "长期下跌":
        return "暂停关注"
    if row.get("long_trend") in {None, "", "数据不足"} or row.get("mid_trend") in {None, "", "数据不足"}:
        return "数据不足"
    if row["long_trend"] == "长期上升" and row["mid_trend"] in {"中期上升", "中期修复"}:
        return "可关注"
    if row["long_trend"] == "长期上升":
        return "仅持有"
    return "观察等待"


def pe_percentile_period(frame):
    pe_rows = frame.dropna(subset=["pe"])
    if len(pe_rows) < PE_MIN_PERIODS:
        return ""
    days = max((pe_rows.iloc[-1]["date"] - pe_rows.iloc[0]["date"]).days, 1)
    years = days / 365.25
    if len(pe_rows) >= PE_WINDOW_ROWS or years >= 10:
        return "近10年"
    rounded_years = max(1, round(years))
    return f"近{rounded_years}年（历史不足10年）"


def build_signals(row):
    signals = [row["overall_status"]]
    for tag in ["长期下跌", "中期下跌", "长期转弱", "短期强势", "短期转弱"]:
        if tag in {row["long_trend"], row["mid_trend"], row["short_trend"]}:
            signals.append(tag)
    if row["asset_type"] == "指数":
        signals.append(row["valuation_status"])
        if row["valuation_status"] == "估值数据缺失":
            signals.append("估值数据缺失")
    return list(dict.fromkeys(signals))


def enrich_history(frame, asset):
    data = add_indicators(frame)
    required = ["MA20", "MA60", "MA120", "MA200", "ma20_slope_5d", "ma60_slope_10d", "ma120_slope_20d", "ma200_slope_40d"]
    for column in ["short_trend", "mid_trend", "long_trend", "overall_status", "valuation_status", "investment_advice", "signal_tags"]:
        data[column] = ""
    data["name"] = asset["name"]
    data["symbol"] = asset["symbol"]
    data["market"] = asset["market"]
    data["asset_type"] = asset["asset_type"]
    data["last_year_dividend"] = asset.get("last_year_dividend", np.nan) if asset["asset_type"] == "股票" else np.nan
    data["dividend_yield"] = (
        data.apply(lambda row: calculate_dividend_yield(row["last_year_dividend"], row["close"]), axis=1)
        if asset["asset_type"] == "股票"
        else np.nan
    )
    data["pe_percentile_period"] = pe_percentile_period(data) if asset["asset_type"] == "指数" else ""
    if asset["asset_type"] == "指数" and "pe_percentile_period_override" in data:
        data["pe_percentile_period"] = data["pe_percentile_period_override"].fillna(data["pe_percentile_period"])
    ready = ~data[required].isna().any(axis=1)
    for idx in data[ready].index:
        row = data.loc[idx]
        data.at[idx, "short_trend"] = determine_short_trend(row)
        data.at[idx, "mid_trend"] = determine_mid_trend(row)
        data.at[idx, "long_trend"] = determine_long_trend(row)
        data.at[idx, "overall_status"] = determine_overall_status(data.loc[idx])
        data.at[idx, "valuation_status"] = valuation_status(row["pe_percentile"]) if asset["asset_type"] == "指数" else ""
        data.at[idx, "investment_advice"] = (
            determine_index_investment_advice(data.loc[idx])
            if asset["asset_type"] == "指数"
            else determine_stock_trend_advice(data.loc[idx])
        )
        data.at[idx, "signal_tags"] = ", ".join(build_signals(data.loc[idx]))
    data.loc[~ready, "overall_status"] = "数据预热"
    if asset["asset_type"] == "指数":
        data.loc[~ready, "valuation_status"] = data.loc[~ready, "pe_percentile"].map(valuation_status)
    return data
