"""High-dividend research assessment helpers.

This module evaluates supplied, versioned facts.  It does not fetch or invent
company data, which prevents an unconfirmed industry extraction becoming a
formal research conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


REQUIRED_COMMON_METRICS = {"revenue", "net_profit", "roe"}


@dataclass(frozen=True)
class FundamentalAssessment:
    dividend_safety_status: str
    operating_quality_status: str
    cash_reinvestment_status: str
    capital_structure_status: str
    fundamental_status: str
    evidence: list[str]
    main_risk: str | None


def _status(value: str | None) -> str:
    return value if value in {"稳健", "关注", "承压", "改善", "稳定", "恶化", "数据不足"} else "数据不足"


def assess_high_dividend_fundamentals(metrics: Mapping[str, float | None], industry_template: str | None) -> FundamentalAssessment:
    """Assess a confirmed latest annual/TTM fact set without cross-industry thresholds.

    Callers supply normalized ratios: payout_ratio, dividend_coverage,
    operating_cashflow_growth, free_cashflow_growth, debt_change,
    capital_ratio (bank) and operating_quality_change.  Missing values remain
    explicit rather than silently reusing a previous report period.
    """

    evidence: list[str] = []
    risk: str | None = None
    payout = metrics.get("payout_ratio")
    coverage = metrics.get("dividend_coverage")
    quality_change = metrics.get("operating_quality_change")
    cash_change = metrics.get("free_cashflow_growth")
    debt_change = metrics.get("debt_change")

    if industry_template == "bank":
        capital = metrics.get("capital_ratio")
        if payout is None or capital is None:
            dividend = "数据不足"
        elif payout <= 0.6 and capital > 0:
            dividend = "稳健"
            evidence.append("分红支付率与资本约束处于可复核范围。")
        else:
            dividend = "关注"
            risk = "需继续核对资本充足率与监管约束对分红的影响。"
        cash = "稳健" if capital is not None and capital > 0 else "数据不足"
        capital_status = "稳健" if capital is not None and capital > 0 else "数据不足"
    else:
        if coverage is None or payout is None:
            dividend = "数据不足"
        elif coverage >= 1 and payout <= 0.8:
            dividend = "稳健"
            evidence.append("经营现金流或自由现金流能够覆盖现金分红。")
        elif coverage > 0:
            dividend = "关注"
            risk = "现金分红覆盖趋窄，需复核后续现金流与支付率。"
        else:
            dividend = "承压"
            risk = "现金分红缺少可验证的现金流覆盖。"
        if cash_change is None:
            cash = "数据不足"
        elif cash_change >= 0:
            cash = "稳健"
        else:
            cash = "关注"
            risk = risk or "自由现金流走弱，可能挤压再投资与股东回报。"
        if debt_change is None:
            capital_status = "数据不足"
        elif debt_change <= 0:
            capital_status = "稳健"
        else:
            capital_status = "关注"
            risk = risk or "债务或利息负担上升，需要跟踪资本结构。"

    if quality_change is None or not REQUIRED_COMMON_METRICS.issubset(metrics):
        quality = "数据不足"
    elif quality_change > 0:
        quality = "改善"
        evidence.append("收入、利润或ROE的核心经营质量指标呈改善。")
    elif quality_change == 0:
        quality = "稳定"
    else:
        quality = "恶化"
        risk = risk or "经营质量指标走弱，需结合行业经营锚点判断可持续性。"

    if "承压" in {dividend, cash, capital_status} or quality == "恶化":
        overall = "恶化"
    elif "数据不足" in {dividend, cash, capital_status, quality}:
        overall = "数据不足"
    elif quality == "改善":
        overall = "改善"
    else:
        overall = "稳定"
    return FundamentalAssessment(dividend, quality, cash, capital_status, overall, evidence[:3], risk)
