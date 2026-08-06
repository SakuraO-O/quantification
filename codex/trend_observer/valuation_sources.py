"""Independent, source-scoped index valuation adapters.

The adapters return normalized observations only.  HTML/JSON responses are
parsed in memory and deliberately never persisted.  A source failure is an
asset-level condition: callers can keep the previous valuation observations
and continue publishing price/trend data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

from .data_sources import make_session
from .market_valuation import NASDAQ_PE_URL, SP500_PE_URL, fetch_text, parse_number, parse_worldperatio


CNI_QUERY_DAY_URL = "https://www.cnindex.com.cn/index/queryDay"
CNI_INDEX_LIST_URL = "https://www.cnindex.com.cn/index/indexList"
CNI_CHANNEL_CODES = {"sz399006": "100", "980092": "204"}


@dataclass(frozen=True)
class ValuationBatch:
    source: str
    source_url: str
    methodology: str
    observations: list[dict]


def valuation_source_name(asset: dict) -> str | None:
    """Return the configured source identifier without making a request."""

    if asset.get("asset_type") != "指数":
        return None
    if asset.get("symbol") in {"sz399006", "980092"}:
        return "cnindex"
    if asset.get("symbol") == "NDX100":
        return "worldperatio"
    if asset.get("symbol") == "SPX":
        return "worldperatio"
    return None


def parse_cnindex_index_list(payload: dict, symbol: str) -> dict:
    """Read the published date and rolling PE from CNI's public JSON API."""

    timestamp = payload.get("query_day")
    if not isinstance(timestamp, (int, float)):
        raise ValueError("国证指数接口缺少公布日期")
    rows = (payload.get("index_list") or {}).get("data", {}).get("rows") or []
    canonical_symbol = symbol.removeprefix("sz")
    row = next((item for item in rows if str(item.get("indexcode") or "") == canonical_symbol), None)
    if row is None:
        raise ValueError(f"国证指数接口未找到 {symbol}")
    value = parse_number(row.get("peDynamic"))
    if value is None or value <= 0:
        raise ValueError(f"国证指数接口未公布 {symbol} 的有效PE(滚动)")
    trade_date = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc).date().isoformat()
    return {"trade_date": trade_date, "value": value}


def fetch_json(session, url: str, *, params: dict | None = None) -> dict:
    """Read a small public JSON payload with bounded retry for transient CDN errors."""

    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=(5, 20))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("估值来源返回的 JSON 不是对象")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    raise ValueError(f"国证指数接口请求失败: {last_error}")


def fetch_cnindex_current_pe(session, asset: dict) -> ValuationBatch:
    channel_code = CNI_CHANNEL_CODES[asset["symbol"]]
    query_day = fetch_json(session, CNI_QUERY_DAY_URL)
    index_list = fetch_json(session, CNI_INDEX_LIST_URL, params={"channelCode": channel_code, "rows": 100, "pageNum": 1})
    observation = parse_cnindex_index_list(
        {"query_day": query_day.get("data"), "index_list": index_list},
        asset["symbol"],
    )
    return ValuationBatch(
        source="cnindex",
        source_url=CNI_INDEX_LIST_URL,
        methodology="official_rolling_pe_current",
        observations=[observation],
    )


def fetch_nasdaq_100_pe(session, _asset: dict) -> ValuationBatch:
    parsed = parse_worldperatio(fetch_text(session, NASDAQ_PE_URL))
    return ValuationBatch(
        source="worldperatio",
        source_url=NASDAQ_PE_URL,
        methodology="estimated_pe_monthly_10y",
        observations=[{"trade_date": point["date"], "value": point["value"]} for point in parsed["history"]],
    )


def fetch_sp500_pe(session, _asset: dict) -> ValuationBatch:
    parsed = parse_worldperatio(
        fetch_text(session, SP500_PE_URL), index_name="标普500", index_pattern=r"S\s*&\s*P\s*500",
    )
    return ValuationBatch(
        source="worldperatio",
        source_url=SP500_PE_URL,
        methodology="estimated_pe_monthly_10y",
        observations=[{"trade_date": point["date"], "value": point["value"]} for point in parsed["history"]],
    )


def fetch_valuation_batch(asset: dict, *, session=None) -> ValuationBatch | None:
    """Return a supported source batch, or ``None`` when no safe free source exists.

    We intentionally leave HSI empty for now: a current value without
    compatible history must not be turned into a made-up percentile.
    """

    source = valuation_source_name(asset)
    if source is None:
        return None
    owned_session = session is None
    session = session or make_session()
    try:
        if source == "cnindex":
            return fetch_cnindex_current_pe(session, asset)
        if source == "worldperatio":
            return fetch_nasdaq_100_pe(session, asset) if asset["symbol"] == "NDX100" else fetch_sp500_pe(session, asset)
        return None
    finally:
        if owned_session:
            session.close()
