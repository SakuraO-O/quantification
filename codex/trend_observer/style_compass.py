"""Multi-period relative-return style compass calculations."""

from __future__ import annotations

import math

import pandas as pd


STYLE_WINDOWS = ((20, 0.2), (60, 0.3), (120, 0.5))


def _return_for_window(frame: pd.DataFrame, window: int) -> float:
    if len(frame) <= window:
        return math.nan
    return float(frame.iloc[-1]["close"] / frame.iloc[-1 - window]["close"] - 1)


def calculate_style_compass(left: pd.DataFrame, right: pd.DataFrame) -> dict:
    """Return raw evidence, score and direction for one fixed comparison pair."""

    left = left.sort_values("date").reset_index(drop=True)
    right = right.sort_values("date").reset_index(drop=True)
    result: dict[str, float | int | str] = {}
    weighted = 0.0
    for window, weight in STYLE_WINDOWS:
        left_return = _return_for_window(left, window)
        right_return = _return_for_window(right, window)
        diff = left_return - right_return
        result[f"return_{window}d_left"] = left_return
        result[f"return_{window}d_right"] = right_return
        result[f"return_{window}d_diff"] = diff
        if math.isnan(diff):
            result["score"] = None
            result["direction"] = "数据不足"
            result["weighted_return_diff"] = math.nan
            return result
        weighted += diff * weight
    score = max(-100, min(100, round(weighted * 100 * 10)))
    result["weighted_return_diff"] = weighted
    result["score"] = score
    result["direction"] = "偏左" if score >= 20 else "偏右" if score <= -20 else "均衡"
    return result


def style_recommendation(direction: str, left_advice: str, right_advice: str) -> str:
    """Apply only investment-advice constraints; no actual amounts are required."""

    eligible = {"优先新增", "可新增"}
    if left_advice not in eligible and right_advice not in eligible:
        return "本组暂不配置新增资金"
    if direction == "偏左" and left_advice in eligible:
        return "新增资金优先关注左侧资产"
    if direction == "偏右" and right_advice in eligible:
        return "新增资金优先关注右侧资产"
    if direction == "均衡":
        return "风格均衡，结合类别配置偏离决定新增方向"
    return "风格占优资产未通过新增约束，暂不倾斜"
