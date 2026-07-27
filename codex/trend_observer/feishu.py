"""Feishu message rendering and webhook delivery."""

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime

import pandas as pd
import requests

from .config import DISCLAIMER, FEISHU_KEYWORD, HTTP_TIMEOUT
from .config import MARKET_TIMEZONE


def format_overall_status(value):
    if value in {"强趋势", "健康上升"}:
        return f"✅{value}"
    if value == "下跌通道":
        return f"❌{value}"
    return value or "--"


def format_feishu_message(snapshot):
    generated_at = snapshot.get("generated_at") or "--"
    lines = [f"{FEISHU_KEYWORD} | 趋势观察报告 | {generated_at}", ""]
    for row in snapshot.get("assets", []):
        if row["asset_type"] == "股票":
            dividend_yield = row.get("dividend_yield")
            if pd.isna(dividend_yield) or not (dividend_yield < 3 or dividend_yield > 5):
                continue

        close = "--" if pd.isna(row.get("close")) else f"{row['close']:.2f}"
        daily_return = "--" if pd.isna(row.get("daily_return")) else f"{row['daily_return']:+.2f}%"
        lines.append(f"{row['name']}（{row['symbol']}）｜{row.get('date') or '--'}｜收盘 {close}｜昨日涨跌幅 {daily_return}")
        lines.append(f"趋势：{row['short_trend']}｜{row['mid_trend']}｜{row['long_trend']}")
        lines.append(f"综合：{format_overall_status(row['overall_status'])}")
        if row["asset_type"] == "指数":
            pe = "--" if pd.isna(row.get("pe")) else f"{row['pe']:.2f}"
            pe_pct = "--" if pd.isna(row.get("pe_percentile")) else f"{row['pe_percentile']:.2f}%"
            lines.append(f"估值：PE {pe}｜百分位 {pe_pct}｜{row.get('valuation_status') or '--'}")
        if row["asset_type"] == "股票":
            lines.append(f"股息率：{row['dividend_yield']:.2f}%")
        if row.get("error"):
            lines.append(f"异常：{row['error']}")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def post_feishu_message(snapshot):
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("未设置 FEISHU_WEBHOOK_URL，无法发送飞书通知。")
    payload = {"msg_type": "text", "content": {"text": format_feishu_message(snapshot)}}
    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        key = f"{timestamp}\n{secret}".encode("utf-8")
        payload.update({"timestamp": timestamp, "sign": base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode("utf-8")})
    response = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if result.get("code", result.get("StatusCode", 0)) != 0:
        raise RuntimeError(f"飞书通知发送失败: {result}")
    print("飞书通知已发送。")


def _dashboard_assets(payload, asset_type):
    return [row for row in payload.get("assets", []) if row.get("asset_type") == asset_type]


def format_dashboard_version_message(version):
    """Render the V2 morning report from a completed dashboard version only."""

    payload = version.get("payload", version)
    latest_date = payload.get("latest_market_date") or "--"
    lines = [f"{FEISHU_KEYWORD} | 趋势观察晨报 | 数据截至 {latest_date}", ""]
    issues = (version.get("completeness") or {}).get("asset_issues") or []
    if issues:
        labels = "、".join(
            f"{item.get('symbol', '--')}（{item.get('reason', '数据延迟')}）"
            for item in issues if isinstance(item, dict)
        )
        lines.extend([f"⚠️ 数据延迟：{labels or '部分资产'}。延迟资产的最新指标已置空，历史数据仍可查看。", ""])
    for row in _dashboard_assets(payload, "指数"):
        if row.get("data_status") == "delayed":
            lines.extend([f"{row['name']}（{row['symbol']}）｜最新数据延迟｜历史截至 {row.get('last_valid_trade_date') or '--'}", ""])
            continue
        close = "--" if pd.isna(row.get("close")) else f"{float(row['close']):.2f}"
        daily_return = "--" if pd.isna(row.get("daily_return")) else f"{float(row['daily_return']) * 100:+.2f}%"
        pe = "--" if pd.isna(row.get("pe")) else f"{float(row['pe']):.2f}"
        pe_pct = "--" if pd.isna(row.get("pe_percentile")) else f"{float(row['pe_percentile']):.2f}%"
        lines.extend(
            [
                f"{row['name']}（{row['symbol']}）｜{row.get('trade_date') or row.get('date') or '--'}｜收盘 {close}｜涨幅 {daily_return}",
                f"趋势：{row.get('short_trend') or '--'}｜{row.get('mid_trend') or '--'}｜{row.get('long_trend') or '--'}",
                f"综合：{format_overall_status(row.get('overall_status'))}｜建议：{row.get('investment_advice') or '--'}",
                f"估值：PE {pe}｜百分位 {pe_pct}｜{row.get('valuation_status') or '--'}",
                "",
            ]
        )
    for row in _dashboard_assets(payload, "股票"):
        if row.get("data_status") == "delayed":
            lines.append(f"{row['name']}（{row['symbol']}）｜最新数据延迟｜历史截至 {row.get('last_valid_trade_date') or '--'}")
            continue
        dividend_yield = row.get("dividend_yield")
        if pd.isna(dividend_yield) or not (float(dividend_yield) < 3 or float(dividend_yield) > 5):
            continue
        close = "--" if pd.isna(row.get("close")) else f"{float(row['close']):.2f}"
        daily_return = "--" if pd.isna(row.get("daily_return")) else f"{float(row['daily_return']) * 100:+.2f}%"
        lines.extend(
            [
                f"{row['name']}（{row['symbol']}）｜{row.get('trade_date') or row.get('date') or '--'}｜收盘 {close}｜涨幅 {daily_return}",
                f"趋势：{row.get('short_trend') or '--'}｜{row.get('mid_trend') or '--'}｜{row.get('long_trend') or '--'}",
                f"综合：{format_overall_status(row.get('overall_status'))}｜股息率：{float(dividend_yield):.2f}%",
                "",
            ]
        )
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines)


def format_delay_message(missing_datasets, now=None):
    now = now or datetime.now(MARKET_TIMEZONE)
    details = "、".join(missing_datasets) if missing_datasets else "必要行情或计算结果"
    return f"{FEISHU_KEYWORD} | 趋势观察数据延迟 | {now:%Y-%m-%d}\n\n{details}尚未形成完整数据版本，完整晨报将在数据就绪后补发。"


def send_text_message(text):
    """Send a text payload and return its HTTP status for dispatch recording."""

    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("未设置 FEISHU_WEBHOOK_URL，无法发送飞书通知。")
    payload = {"msg_type": "text", "content": {"text": text}}
    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        key = f"{timestamp}\n{secret}".encode("utf-8")
        payload.update({"timestamp": timestamp, "sign": base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode("utf-8")})
    response = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if result.get("code", result.get("StatusCode", 0)) != 0:
        raise RuntimeError(f"飞书通知发送失败: {result}")
    return response.status_code
