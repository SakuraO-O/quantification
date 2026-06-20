"""Market data fetching and source routing."""

import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

from .config import HTTP_TIMEOUT, MARKET_TIMEZONE, MIN_HISTORY_ROWS, TENCENT_LOOKBACK_DAYS, TENCENT_LOOKBACK_ROWS


def make_session():
    session = requests.Session()
    session.trust_env = True
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://www.csindex.com.cn/",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


def normalize_frame(rows):
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "pe"])
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ["open", "high", "low", "close", "volume", "pe"]:
        if column not in frame:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "high", "low", "close"])
    return frame[["date", "open", "high", "low", "close", "volume", "pe"]].sort_values("date").drop_duplicates("date").reset_index(drop=True)


def fetch_tencent(session, symbol):
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = today - timedelta(days=TENCENT_LOOKBACK_DAYS)
    params = {"param": f"{symbol},day,{start:%Y-%m-%d},{today:%Y-%m-%d},{TENCENT_LOOKBACK_ROWS},qfq"}
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    symbol_data = payload.get("data", {}).get(symbol, {})
    raw_rows = symbol_data.get("qfqday") or symbol_data.get("day") or []
    rows = [
        {
            "date": row[0],
            "open": row[1],
            "close": row[2],
            "high": row[3],
            "low": row[4],
            "volume": row[5] if len(row) > 5 else np.nan,
            "pe": np.nan,
        }
        for row in raw_rows
    ]
    return normalize_frame(rows)


def eastmoney_secid(symbol):
    code = symbol[2:] if symbol[:2] in {"sh", "sz"} else symbol
    if symbol.startswith("sh"):
        return f"1.{code}"
    if symbol.startswith("sz"):
        return f"0.{code}"
    raise ValueError(f"东方财富暂不支持该代码: {symbol}")


def fetch_eastmoney_stock(session, symbol):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = today - timedelta(days=TENCENT_LOOKBACK_DAYS)
    params = {
        "secid": eastmoney_secid(symbol),
        "klt": "101",
        "fqt": "1",
        "beg": start.strftime("%Y%m%d"),
        "end": today.strftime("%Y%m%d"),
        "lmt": TENCENT_LOOKBACK_ROWS,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    raw_rows = payload.get("data", {}).get("klines") or []
    rows = []
    for raw in raw_rows:
        parts = raw.split(",")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": parts[1],
                "close": parts[2],
                "high": parts[3],
                "low": parts[4],
                "volume": parts[5],
                "pe": np.nan,
            }
        )
    return normalize_frame(rows)


def fetch_csindex(session, symbol):
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = today - timedelta(days=365 * 11)
    params = {"indexCode": symbol, "startDate": start.strftime("%Y%m%d"), "endDate": today.strftime("%Y%m%d")}
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("code")) != "200":
                raise ValueError(f"中证指数官网返回异常: {payload}")
            rows = payload.get("data") or []
            if not rows:
                raise ValueError(f"中证指数官网未返回行情: {symbol}")
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise last_error
            time.sleep(0.8 * (attempt + 1))
    return normalize_frame(
        [
            {
                "date": row.get("tradeDate"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("tradingVol", np.nan),
                "pe": row.get("peg", np.nan),
            }
            for row in rows
        ]
    )


def fetch_cnindex(session, symbol):
    url = "https://hq.cnindex.com.cn/market/market/getIndexDailyData"
    response = session.get(url, params={"indexCode": symbol}, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise ValueError(f"国证指数官网返回异常: {payload}")
    data = payload.get("data") or {}
    column_index = {name: index for index, name in enumerate(data.get("item") or [])}
    required = {"timestamp", "open", "close", "high", "low"}
    if not required.issubset(column_index):
        raise ValueError(f"国证指数官网返回字段缺失: {symbol}")
    rows = []
    for row in data.get("data") or []:
        rows.append(
            {
                "date": datetime.fromtimestamp(row[column_index["timestamp"]] / 1000, MARKET_TIMEZONE).strftime("%Y-%m-%d"),
                "open": row[column_index["open"]],
                "close": row[column_index["close"]],
                "high": row[column_index["high"]],
                "low": row[column_index["low"]],
                "volume": row[column_index["volume"]] if "volume" in column_index else np.nan,
                "pe": np.nan,
            }
        )
    return normalize_frame(rows)


def fetch_history(session, asset):
    provider = asset["provider"]
    if provider == "tencent" and asset["asset_type"] == "股票":
        try:
            history = fetch_eastmoney_stock(session, asset["symbol"])
            if len(history) >= MIN_HISTORY_ROWS:
                return history
        except Exception:
            pass
        return fetch_tencent(session, asset["symbol"])
    if provider == "tencent":
        return fetch_tencent(session, asset["symbol"])
    if provider == "csindex":
        return fetch_csindex(session, asset["symbol"])
    if provider == "cnindex":
        return fetch_cnindex(session, asset["symbol"])
    raise ValueError(f"不支持的数据源: {provider}")

