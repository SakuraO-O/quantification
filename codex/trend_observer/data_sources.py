"""Market data fetching and source routing."""

import time
from datetime import datetime, timedelta
from urllib.parse import quote

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


def with_source(frame, provider):
    frame.attrs["source_provider"] = provider
    return frame


def array_value(values, index):
    if not values or index >= len(values):
        return np.nan
    return values[index]


def parse_market_number(value):
    if value in (None, "", "--"):
        return np.nan
    return str(value).replace(",", "").replace("$", "")


def _start_date(start_date, default_days):
    if start_date is None:
        return datetime.now(MARKET_TIMEZONE).date() - timedelta(days=default_days)
    return pd.Timestamp(start_date).date()


def fetch_tencent(session, symbol, start_date=None):
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = _start_date(start_date, TENCENT_LOOKBACK_DAYS)
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


def fetch_eastmoney_stock(session, symbol, start_date=None):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = _start_date(start_date, TENCENT_LOOKBACK_DAYS)
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


def fetch_eastmoney_global_index(session, symbol, start_date=None):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = _start_date(start_date, TENCENT_LOOKBACK_DAYS)
    params = {
        "secid": symbol,
        "klt": "101",
        "fqt": "0",
        "beg": start.strftime("%Y%m%d"),
        "end": today.strftime("%Y%m%d"),
        "lmt": TENCENT_LOOKBACK_ROWS,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, headers={"Referer": "https://quote.eastmoney.com/"}, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            raw_rows = (payload.get("data") or {}).get("klines") or []
            if not raw_rows:
                raise ValueError(f"东方财富全球指数未返回行情: {symbol}")
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise last_error
            time.sleep(0.8 * (attempt + 1))
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


def fetch_yahoo_index(session, symbol, start_date=None):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    today = datetime.now(MARKET_TIMEZONE)
    start = _start_date(start_date, TENCENT_LOOKBACK_DAYS)
    params = {
        "period1": int(datetime.combine(start, datetime.min.time(), tzinfo=MARKET_TIMEZONE).timestamp()),
        "period2": int(today.timestamp()),
        "interval": "1d",
        "events": "history",
    }
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, headers={"Referer": "https://finance.yahoo.com/"}, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            chart = payload.get("chart") or {}
            error = chart.get("error")
            if error:
                raise ValueError(f"Yahoo Finance 返回异常: {error}")
            results = chart.get("result") or []
            if not results:
                raise ValueError(f"Yahoo Finance 未返回行情: {symbol}")
            result = results[0]
            timestamps = result.get("timestamp") or []
            quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            if not timestamps:
                raise ValueError(f"Yahoo Finance 未返回行情: {symbol}")
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise last_error
            time.sleep(0.8 * (attempt + 1))
    rows = []
    for index, timestamp in enumerate(timestamps):
        rows.append(
            {
                "date": datetime.fromtimestamp(timestamp, MARKET_TIMEZONE).strftime("%Y-%m-%d"),
                "open": array_value(quote_data.get("open"), index),
                "close": array_value(quote_data.get("close"), index),
                "high": array_value(quote_data.get("high"), index),
                "low": array_value(quote_data.get("low"), index),
                "volume": array_value(quote_data.get("volume"), index),
                "pe": np.nan,
            }
        )
    return normalize_frame(rows)


def fetch_nasdaq_index(session, symbol, start_date=None):
    url = f"https://api.nasdaq.com/api/quote/{symbol}/historical"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = _start_date(start_date, TENCENT_LOOKBACK_DAYS)
    params = {
        "assetclass": "index",
        "fromdate": start.isoformat(),
        "todate": today.isoformat(),
        "limit": TENCENT_LOOKBACK_ROWS,
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": f"https://www.nasdaq.com/market-activity/index/{symbol.lower()}/historical",
    }
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status") or {}
            if status.get("rCode") not in (None, 200):
                raise ValueError(f"Nasdaq 返回异常: {status}")
            rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
            if not rows:
                raise ValueError(f"Nasdaq 未返回行情: {symbol}")
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise last_error
            time.sleep(0.8 * (attempt + 1))
    return normalize_frame(
        [
            {
                "date": datetime.strptime(row.get("date"), "%m/%d/%Y").strftime("%Y-%m-%d"),
                "open": parse_market_number(row.get("open")),
                "close": parse_market_number(row.get("close")),
                "high": parse_market_number(row.get("high")),
                "low": parse_market_number(row.get("low")),
                "volume": parse_market_number(row.get("volume")),
                "pe": np.nan,
            }
            for row in rows
        ]
    )


def fetch_global_index(session, asset, start_date=None):
    errors = []
    candidates = [
        ("东方财富全球指数", "eastmoney", fetch_eastmoney_global_index, asset.get("eastmoney_symbol")),
        ("Nasdaq", "nasdaq", fetch_nasdaq_index, asset.get("nasdaq_symbol")),
        ("Yahoo Finance", "yahoo", fetch_yahoo_index, asset.get("yahoo_symbol")),
    ]
    for label, provider, fetcher, symbol in candidates:
        if not symbol:
            continue
        try:
            history = fetcher(session, symbol, start_date=start_date)
            if not history.empty and (start_date is not None or len(history) >= MIN_HISTORY_ROWS):
                return with_source(history, provider)
            errors.append(f"{label} 仅返回 {len(history)} 个交易日")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    raise ValueError(f"全球指数行情获取失败: {'; '.join(errors)}")


def fetch_csindex(session, symbol, start_date=None):
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = _start_date(start_date, 365 * 11)
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


def fetch_cnindex(session, symbol, start_date=None):
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
    frame = normalize_frame(rows)
    if start_date is not None:
        frame = frame[frame["date"] >= pd.Timestamp(start_date)].reset_index(drop=True)
    return frame


def fetch_history(session, asset, start_date=None):
    provider = asset["provider"]
    if provider == "tencent" and asset["asset_type"] == "股票":
        try:
            history = fetch_eastmoney_stock(session, asset["symbol"], start_date=start_date)
            if not history.empty and (start_date is not None or len(history) >= MIN_HISTORY_ROWS):
                return with_source(history, "eastmoney")
        except Exception:
            pass
        return with_source(fetch_tencent(session, asset["symbol"], start_date=start_date), "tencent")
    if provider == "tencent":
        return with_source(fetch_tencent(session, asset["symbol"], start_date=start_date), "tencent")
    if provider == "csindex":
        return with_source(fetch_csindex(session, asset["symbol"], start_date=start_date), "csindex")
    if provider == "cnindex":
        return with_source(fetch_cnindex(session, asset["symbol"], start_date=start_date), "cnindex")
    if provider == "global_index":
        return fetch_global_index(session, asset, start_date=start_date)
    raise ValueError(f"不支持的数据源: {provider}")
