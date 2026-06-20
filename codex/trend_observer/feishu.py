"""Feishu message rendering and webhook delivery."""

import base64
import hashlib
import hmac
import os
import time

import pandas as pd
import requests

from .config import DISCLAIMER, FEISHU_KEYWORD, HTTP_TIMEOUT


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
        lines.append(f"{row['name']}（{row['symbol']}）｜{row.get('date') or '--'}｜收盘 {close}")
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

