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


def style_recommendation_details(
    direction: str,
    left_advice: str | None,
    left_pe_percentile: float | None,
    right_advice: str | None,
    right_pe_percentile: float | None,
) -> dict[str, str]:
    """Return the single publishable style-allocation conclusion and reason.

    A relative-return direction alone never authorizes a tilt.  The winning
    side must be eligible for new capital and have a non-missing PE percentile
    no greater than 50%, matching the dashboard rule.
    """

    eligible = {"优先新增", "可新增"}
    if direction == "数据不足":
        return {"recommendation": "数据不足", "recommendation_reason": "比较所需的历史行情不足，暂不判断风格倾斜。"}
    if direction == "均衡":
        return {"recommendation": "保持均衡", "recommendation_reason": "多周期收益差未形成显著方向。"}
    if direction not in {"偏左", "偏右"}:
        return {"recommendation": "数据不足", "recommendation_reason": "风格方向无效，暂不判断风格倾斜。"}

    side = "left" if direction == "偏左" else "right"
    advice = left_advice if side == "left" else right_advice
    percentile = left_pe_percentile if side == "left" else right_pe_percentile
    side_name = "左侧" if side == "left" else "右侧"
    if advice not in eligible:
        return {"recommendation": "暂不倾斜", "recommendation_reason": f"占优侧投资建议为“{advice or '数据不足'}”，未通过新增约束。"}
    if percentile is None or not math.isfinite(float(percentile)):
        return {"recommendation": "暂不倾斜", "recommendation_reason": "占优侧PE百分位缺失，未通过估值约束。"}
    if float(percentile) > 50:
        return {"recommendation": "暂不倾斜", "recommendation_reason": f"占优侧PE百分位为{float(percentile):.2f}%，高于50%，未通过估值约束。"}
    return {
        "recommendation": f"新增资金优先关注{side_name}资产",
        "recommendation_reason": "风格占优，且占优侧趋势建议与PE百分位均通过新增约束。",
    }


def style_recommendation(
    direction: str,
    left_advice: str | None,
    right_advice: str | None,
    left_pe_percentile: float | None = None,
    right_pe_percentile: float | None = None,
) -> str:
    """Compatibility wrapper returning only the publishable recommendation."""

    return style_recommendation_details(
        direction, left_advice, left_pe_percentile, right_advice, right_pe_percentile
    )["recommendation"]
