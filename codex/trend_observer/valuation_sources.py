"""Independent, source-scoped index valuation adapters.

The adapters return normalized observations only.  HTML/JSON responses are
parsed in memory and deliberately never persisted.  A source failure is an
asset-level condition: callers can keep the previous valuation observations
and continue publishing price/trend data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from bs4 import BeautifulSoup

from .data_sources import make_session
from .market_valuation import NASDAQ_PE_URL, fetch_text, parse_number, parse_worldperatio


CNI_SHENZHEN_INDEX_URL = "https://www.cnindex.com.cn/zh_indices/sese/index.html?act_menu=1&index_type=-1"
CNI_STRATEGY_INDEX_URL = "https://www.cnindex.com.cn/zh_indices/cni/strategy/index.html?act_menu=null&index_type=204"


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
    return None


def _date_from_text(text: str) -> str:
    match = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", text)
    if not match:
        raise ValueError("来源页面缺少估值日期")
    return "-".join(match.groups())


def parse_cnindex_current_pe(html: str, symbol: str) -> dict:
    """Extract one officially published rolling P/E from a CNI index table."""

    soup = BeautifulSoup(html, "html.parser")
    source_date = _date_from_text(" ".join(soup.stripped_strings))
    canonical_symbol = symbol.removeprefix("sz").lstrip("0") or "0"
    for table in soup.find_all("table"):
        rows = [
            [" ".join(cell.stripped_strings) for cell in row.find_all(["th", "td"])]
            for row in table.find_all("tr")
        ]
        if not rows:
            continue
        header_index = next((index for index, row in enumerate(rows) if "指数代码" in row and any("PE(滚动)" in cell for cell in row)), None)
        if header_index is None:
            continue
        header = rows[header_index]
        code_index = header.index("指数代码")
        pe_index = next(index for index, cell in enumerate(header) if "PE(滚动)" in cell)
        for row in rows[header_index + 1 :]:
            if max(code_index, pe_index) >= len(row):
                continue
            row_symbol = re.sub(r"\D", "", row[code_index]).lstrip("0") or "0"
            if row_symbol != canonical_symbol:
                continue
            value = parse_number(row[pe_index])
            if value is None or value <= 0:
                raise ValueError(f"国证指数网未公布 {symbol} 的有效PE(滚动)")
            return {"trade_date": source_date, "value": value}
    raise ValueError(f"国证指数网页面未找到 {symbol} 的PE(滚动)")


def fetch_cnindex_current_pe(session, asset: dict) -> ValuationBatch:
    url = CNI_SHENZHEN_INDEX_URL if asset["symbol"] == "sz399006" else CNI_STRATEGY_INDEX_URL
    observation = parse_cnindex_current_pe(fetch_text(session, url), asset["symbol"])
    return ValuationBatch(
        source="cnindex",
        source_url=url,
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


def fetch_valuation_batch(asset: dict, *, session=None) -> ValuationBatch | None:
    """Return a supported source batch, or ``None`` when no safe free source exists.

    We intentionally leave SPX and HSI empty for now: a current value without
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
            return fetch_nasdaq_100_pe(session, asset)
        return None
    finally:
        if owned_session:
            session.close()
