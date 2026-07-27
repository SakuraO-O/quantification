"""Test-only mirror of the database-owned allocation calculation contract.

Production configuration is calculated exclusively by
``public.compute_portfolio_allocation()`` in Supabase.  This small pure-Python
mirror exists only to exercise boundary fixtures without requiring a database
in unit tests; it must not be imported by publishing, Edge, or UI code.
"""

from __future__ import annotations

from .config import PORTFOLIO_CATEGORIES


def calculate_allocation(target_ratios: dict[str, float], actual_amounts: dict[str, float]) -> list[dict]:
    """Mirror the RPC rows for unit-test fixtures, not production rendering."""

    unknown = (set(target_ratios) | set(actual_amounts)) - set(PORTFOLIO_CATEGORIES)
    if unknown:
        raise ValueError(f"存在不支持的配置类别: {', '.join(sorted(unknown))}")
    if any(value < 0 for value in target_ratios.values()) or any(value < 0 for value in actual_amounts.values()):
        raise ValueError("目标比例和实际金额不能为负数。")
    total_target = sum(target_ratios.get(category, 0) for category in PORTFOLIO_CATEGORIES)
    if round(total_target, 1) != 100.0:
        raise ValueError(f"目标配置比例合计须等于100%，当前合计为{total_target:.1f}%。")
    total_amount = sum(actual_amounts.get(category, 0) for category in PORTFOLIO_CATEGORIES)
    if total_amount <= 0:
        raise ValueError("至少一个类别的实际配置金额须大于0。")

    rows = []
    for category in PORTFOLIO_CATEGORIES:
        target_ratio = float(target_ratios.get(category, 0))
        actual_amount = float(actual_amounts.get(category, 0))
        actual_ratio = actual_amount / total_amount * 100
        deviation = actual_ratio - target_ratio
        absolute_deviation = abs(deviation)
        if absolute_deviation <= 2:
            state = "均衡"
        elif absolute_deviation <= 5:
            state = "关注"
        else:
            state = "明显超配" if deviation > 0 else "明显低配"
        rows.append(
            {
                "category": category,
                "target_ratio": round(target_ratio, 1),
                "actual_amount": round(actual_amount, 2),
                "actual_ratio": round(actual_ratio, 1),
                "deviation": round(deviation, 1),
                "deviation_state": state,
                "theoretical_adjustment_amount": round(total_amount * target_ratio / 100 - actual_amount, 2),
            }
        )
    return rows
