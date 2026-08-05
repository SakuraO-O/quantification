"""Independent market valuation and interest-rate snapshot fetching."""

import json
import os
import re
import tempfile
import time
from calendar import monthrange
from datetime import date, datetime

from bs4 import BeautifulSoup

from .config import HTTP_TIMEOUT, MARKET_TIMEZONE, MARKET_VALUATION_OUTPUT
from .data_sources import make_session


CHINABOND_URL = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_ZH"
HS300_PE_URL = "https://www.lixinger.com/equity/index/detail/sh/000300/300/fundamental/valuation/pe-ttm"
HS300_DIVIDEND_URL = "https://www.lixinger.com/equity/index/detail/sh/000300/300/fundamental/valuation/dyr"
CHINEXT_PE_URL = "https://www.lixinger.com/equity/index/detail/sz/399006/399006/fundamental/valuation/pe-ttm"
NASDAQ_PE_URL = "https://worldperatio.com/index/nasdaq-100/"
SP500_PE_URL = "https://worldperatio.com/index/sp-500/"
DIVIDEND_LOW_VOLATILITY_URL = "https://www.lixinger.com/equity/index/detail/csi/H30269/1730269/fundamental/valuation/dyr"
HS300_EQUITY_BOND_SPREAD_URL = "https://baifenwei.com/indicator/equity-bond-spread/hs300/"


def fetched_at_now(now=None):
    current = now or datetime.now(MARKET_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MARKET_TIMEZONE)
    return current.isoformat(timespec="seconds")


def normalized_text(html):
    return " ".join(BeautifulSoup(html, "html.parser").stripped_strings)


def parse_number(value, percent=False):
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "").replace("%", "")
    if cleaned in {"", "--", "null", "None"}:
        return None
    number = float(cleaned)
    if percent and not 0 <= number <= 100:
        raise ValueError(f"百分数超出0至100范围: {number}")
    return number


def parse_iso_date(value):
    return datetime.strptime(value.strip(), "%Y-%m-%d").date().isoformat()


def fetch_text(session, url):
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            if not response.text.strip():
                raise ValueError("页面内容为空")
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    raise ValueError(f"请求失败: {last_error}")


def table_rows(soup):
    for table in soup.find_all("table"):
        rows = []
        for row in table.find_all("tr"):
            # ChinaBond splits labels such as "10年" with HTML comments.
            cells = ["".join(cell.stripped_strings) for cell in row.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if rows:
            yield rows


def parse_chinabond(html):
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    date_match = re.search(r"(20\d{2}-\d{2}-\d{2})\s*\(%\)", text) or re.search(
        r"日期\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})", text
    )
    if not date_match:
        raise ValueError("ChinaBond页面缺少最新数据日期")
    for rows in table_rows(soup):
        header_index = next((i for i, row in enumerate(rows) if "10年" in row), None)
        if header_index is None:
            continue
        column = rows[header_index].index("10年")
        for row in rows[header_index + 1 :]:
            if row and row[0] == "中债国债收益率曲线":
                if column >= len(row):
                    raise ValueError("ChinaBond国债收益率行缺少10年列")
                return parse_number(row[column], percent=True), parse_iso_date(date_match.group(1))
    raise ValueError("ChinaBond页面未找到中债国债收益率曲线的10年列")


def validate_lixinger_page(text, expected_name, metric):
    if expected_name not in text:
        raise ValueError(f"理杏仁页面指数不匹配，预期: {expected_name}")
    if "市值加权" not in text:
        raise ValueError("理杏仁页面未明确标注市值加权口径")
    heading = "历史市盈率" if metric == "pe" else "历史股息率"
    if heading not in text:
        raise ValueError(f"理杏仁页面指标不匹配，预期: {heading}")


def parse_lixinger_latest(text, percent=False):
    date_match = re.search(r"最后更新于\s*[:：]\s*(20\d{2}-\d{2}-\d{2})", text)
    value_match = re.search(r"当前值\s*[:：]\s*([\d,.]+)\s*(%?)", text)
    if not date_match or not value_match:
        raise ValueError("理杏仁页面缺少当前值或更新日期")
    if percent and value_match.group(2) != "%":
        raise ValueError("理杏仁股息率当前值缺少百分号")
    return parse_number(value_match.group(1), percent=percent), parse_iso_date(date_match.group(1))


def explicit_lixinger_10y_percentile(soup):
    text = " ".join(soup.stripped_strings)
    visible_match = re.search(
        r"(?:近\s*)?10\s*年(?:范围|时间范围|百分位)?\s*[:：-]?\s*当前分位点\s*[:：]?\s*([\d,.]+)\s*%",
        text,
    )
    if visible_match:
        return parse_number(visible_match.group(1), percent=True)
    source = str(soup)
    patterns = [
        r'"(?:range|period)"\s*:\s*"(?:10y|10Y|10年)"[^{}]{0,300}"(?:currentPercentile|percentile)"\s*:\s*([\d.]+)',
        r'"(?:currentPercentile|percentile)"\s*:\s*([\d.]+)[^{}]{0,300}"(?:range|period)"\s*:\s*"(?:10y|10Y|10年)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return parse_number(match.group(1), percent=True)
    for string_node in soup.find_all(string=re.compile(r"当前分位点")):
        element = string_node.parent
        for _ in range(4):
            if element is None or element.name in {"body", "html", "[document]"}:
                break
            attributes = " ".join(str(value) for value in element.attrs.values())
            if re.search(r"(?:10y|10-year|10年)", attributes, re.IGNORECASE):
                local_text = " ".join(element.stripped_strings)
                match = re.search(r"当前分位点\s*[:：]?\s*([\d,.]+)\s*%", local_text)
                if match:
                    return parse_number(match.group(1), percent=True)
            element = element.parent
    return None


def parse_lixinger_pe(html, expected_name):
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    validate_lixinger_page(text, expected_name, "pe")
    value, value_date = parse_lixinger_latest(text)
    return value, explicit_lixinger_10y_percentile(soup), value_date


def parse_lixinger_dividend(html, expected_name):
    text = normalized_text(html)
    validate_lixinger_page(text, expected_name, "dividend")
    return parse_lixinger_latest(text, percent=True)


def subtract_years(value, years):
    target_year = value.year - years
    target_day = min(value.day, monthrange(target_year, value.month)[1])
    return date(target_year, value.month, target_day)


def parse_worldperatio(html, *, index_name="Nasdaq 100", index_pattern=r"Nasdaq\s*100"):
    text = normalized_text(html)
    # The generic narrative fallback below is intentionally accepted only
    # after the page itself has been identified.  A redirect or cache mix-up
    # must not turn another index's PE into the requested index observation.
    if not re.search(index_pattern, text, re.IGNORECASE):
        raise ValueError(f"WorldPERatio页面与预期指数不匹配: {index_name}")
    match = re.search(
        rf"{index_pattern}\s*Index\s*P/E\s*Ratio\s*([\d,.]+)\s*(\d{{2}}\s+[A-Za-z]+\s+20\d{{2}})",
        text,
        re.IGNORECASE,
    ) or re.search(
        r"estimated\s+Price-to-Earnings.*?is\s+([\d,.]+),\s+calculated\s+on\s+(\d{2}\s+[A-Za-z]+\s+20\d{2})",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"WorldPERatio页面缺少{index_name}当前PE或日期")
    current_pe = parse_number(match.group(1))
    current_date = datetime.strptime(match.group(2), "%d %B %Y").date()
    series_match = re.search(r"detailPE_data\s*=\s*\[(.*?)\]\s*;", html, re.DOTALL)
    if not series_match:
        raise ValueError("WorldPERatio页面缺少detailPE_data历史序列")
    point_pattern = re.compile(
        r"\[\s*Date\.UTC\(\s*(20\d{2})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)\s*,\s*([\d.]+)\s*\]"
    )
    points = {}
    for year, month, day, value in point_pattern.findall(series_match.group(1)):
        point_date = date(int(year), int(month) + 1, int(day))
        point_value = parse_number(value)
        if point_value and point_value > 0:
            points[point_date] = point_value
    cutoff = subtract_years(current_date, 10)
    recent = [(point_date, value) for point_date, value in sorted(points.items()) if cutoff < point_date <= current_date][-120:]
    if len(recent) < 120:
        raise ValueError(f"WorldPERatio近10年有效历史PE不足120个月，实际{len(recent)}个月")
    months = [point_date.year * 12 + point_date.month for point_date, _ in recent]
    current_month = current_date.year * 12 + current_date.month
    if months != list(range(current_month - 119, current_month + 1)):
        raise ValueError("WorldPERatio近10年历史PE月份不连续或未覆盖当前月份")
    # The page's headline is the current observation, while the embedded
    # series is month-end history.  Replace the current month's historical
    # point so the stored latest PE and its percentile use the same value.
    # Appending it would turn a 120-month window into 121 observations.
    recent[-1] = (current_date, current_pe)
    percentile = sum(value <= current_pe for _, value in recent) / len(recent) * 100
    return {
        "pe": current_pe,
        "date": current_date.isoformat(),
        "percentile_10y": round(percentile, 2),
        "history_start": recent[0][0].isoformat(),
        "history_end": recent[-1][0].isoformat(),
        "history_count": len(recent),
        # Kept as normalized values only.  Callers may persist these facts,
        # never the HTML response from which they were parsed.
        "history": [{"date": point_date.isoformat(), "value": value} for point_date, value in recent],
    }


def extract_labeled_value(text, label, percent=False):
    match = re.search(rf"{re.escape(label)}\s*[:：]?\s*([\d,.]+)\s*(%?)", text)
    if not match:
        raise ValueError(f"页面缺少字段: {label}")
    if percent and match.group(2) != "%":
        raise ValueError(f"百分数字段缺少百分号: {label}")
    return parse_number(match.group(1), percent=percent)


def parse_baifenwei(html):
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    date_match = re.search(r"最新交易日\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})", text)
    if not date_match:
        raise ValueError("百分位页面缺少最新交易日")
    pe = extract_labeled_value(text, "PE-TTM（市值加权）")
    spread = extract_labeled_value(text, "股债利差", percent=True)
    percentile = None
    for rows in table_rows(soup):
        header_index = next((i for i, row in enumerate(rows) if "近10年" in row), None)
        if header_index is None:
            continue
        column = rows[header_index].index("近10年")
        for row in rows[header_index + 1 :]:
            if row and row[0] == "当前百分位" and column < len(row):
                percentile = parse_number(row[column], percent=True)
                break
    if percentile is None:
        match = re.search(r"时间跨度\s+近10年(?:\s+近\d+年)+\s+当前百分位\s+([\d,.]+)\s*%", text)
        if match:
            percentile = parse_number(match.group(1), percent=True)
    if percentile is None:
        raise ValueError("百分位页面未找到近10年股债利差百分位")
    return spread, percentile, pe, parse_iso_date(date_match.group(1))


def meta(method, source, url, fetched_at, error=None):
    return {"method": method, "source": source, "source_url": url, "fetched_at": fetched_at, "error": error}


def collect_market_valuation_snapshot(session=None, now=None):
    fetched_at = fetched_at_now(now)
    owns_session = session is None
    session = session or make_session()
    indicators = {}
    try:
        method, source, url = "中债国债收益率曲线待偿期10年收益率", "ChinaBond", CHINABOND_URL
        try:
            value, value_date = parse_chinabond(fetch_text(session, url))
            error = None
        except Exception as exc:
            value, value_date, error = None, None, str(exc)
        indicators["china_10y_bond"] = meta(method, source, url, fetched_at, error) | {
            "value": value, "date": value_date, "china_10y_bond_yield": value,
            "china_10y_bond_yield_date": value_date, "china_10y_bond_yield_method": method,
            "china_10y_bond_yield_source": source, "china_10y_bond_yield_source_url": url,
            "china_10y_bond_yield_fetched_at": fetched_at,
        }

        method, source, url = "市值加权PE-TTM", "Lixinger", HS300_PE_URL
        try:
            pe, percentile, value_date = parse_lixinger_pe(fetch_text(session, url), "沪深300")
            error = None if percentile is not None else "理杏仁页面未明确提供近10年当前分位点"
        except Exception as exc:
            pe, percentile, value_date, error = None, None, None, str(exc)
        indicators["hs300_valuation"] = meta(method, source, url, fetched_at, error) | {
            "value": pe, "date": value_date, "hs300_pe_ttm": pe,
            "hs300_pe_ttm_percentile_10y": percentile, "hs300_pe_ttm_date": value_date,
            "hs300_pe_ttm_method": method, "hs300_pe_ttm_source": source,
            "hs300_pe_ttm_source_url": url, "hs300_pe_ttm_fetched_at": fetched_at,
        }

        method, source, url = "市值加权动态股息率", "Lixinger", HS300_DIVIDEND_URL
        try:
            value, value_date = parse_lixinger_dividend(fetch_text(session, url), "沪深300")
            error = None
        except Exception as exc:
            value, value_date, error = None, None, str(exc)
        indicators["hs300_dividend"] = meta(method, source, url, fetched_at, error) | {
            "value": value, "date": value_date, "hs300_dividend_yield": value,
            "hs300_dividend_yield_date": value_date, "hs300_dividend_yield_method": method,
            "hs300_dividend_yield_source": source, "hs300_dividend_yield_source_url": url,
            "hs300_dividend_yield_fetched_at": fetched_at,
        }

        method, source, url = "市值加权PE-TTM", "Lixinger", CHINEXT_PE_URL
        try:
            pe, percentile, value_date = parse_lixinger_pe(fetch_text(session, url), "创业板指")
            error = None if percentile is not None else "理杏仁页面未明确提供近10年当前分位点"
        except Exception as exc:
            pe, percentile, value_date, error = None, None, None, str(exc)
        indicators["chinext_100_valuation"] = meta(method, source, url, fetched_at, error) | {
            "value": pe, "date": value_date, "chinext_100_name": "创业板100", "chinext_100_symbol": "399006",
            "chinext_100_pe_ttm": pe, "chinext_100_pe_ttm_percentile_10y": percentile,
            "chinext_100_pe_ttm_date": value_date, "chinext_100_pe_ttm_method": method,
            "chinext_100_pe_ttm_source": source, "chinext_100_pe_ttm_source_url": url,
            "chinext_100_pe_ttm_fetched_at": fetched_at,
        }

        method, source, url = "WorldPERatio同源历史PE计算", "WorldPERatio", NASDAQ_PE_URL
        try:
            nasdaq = parse_worldperatio(fetch_text(session, url))
            error = None
        except Exception as exc:
            nasdaq = {"pe": None, "date": None, "percentile_10y": None, "history_start": None, "history_end": None, "history_count": 0}
            error = str(exc)
        indicators["nasdaq_100_valuation"] = meta(method, source, url, fetched_at, error) | {
            "value": nasdaq["pe"], "date": nasdaq["date"], "nasdaq_100_symbol": "NDX",
            "nasdaq_100_pe": nasdaq["pe"], "nasdaq_100_pe_percentile_10y": nasdaq["percentile_10y"],
            "nasdaq_100_pe_date": nasdaq["date"], "nasdaq_100_pe_method": method,
            "nasdaq_100_pe_source": source, "nasdaq_100_pe_source_url": url,
            "nasdaq_100_pe_history_start": nasdaq["history_start"], "nasdaq_100_pe_history_end": nasdaq["history_end"],
            "nasdaq_100_pe_history_count": nasdaq["history_count"], "nasdaq_100_pe_fetched_at": fetched_at,
            "nasdaq_100_pe_error": error,
        }

        method, source, url = "市值加权动态股息率", "Lixinger", DIVIDEND_LOW_VOLATILITY_URL
        try:
            value, value_date = parse_lixinger_dividend(fetch_text(session, url), "红利低波")
            error = None
        except Exception as exc:
            value, value_date, error = None, None, str(exc)
        indicators["dividend_low_volatility"] = meta(method, source, url, fetched_at, error) | {
            "value": value, "date": value_date, "dividend_low_volatility_symbol": "H30269",
            "dividend_low_volatility_dividend_yield": value,
            "dividend_low_volatility_dividend_yield_date": value_date,
            "dividend_low_volatility_dividend_yield_method": method,
            "dividend_low_volatility_dividend_yield_source": source,
            "dividend_low_volatility_dividend_yield_source_url": url,
            "dividend_low_volatility_dividend_yield_fetched_at": fetched_at,
        }

        method = "页面公布值：100/沪深300市值加权PE-TTM - 中国十年期国债收益率"
        source, url = "Baifenwei", HS300_EQUITY_BOND_SPREAD_URL
        try:
            spread, percentile, pe, value_date = parse_baifenwei(fetch_text(session, url))
            error = None
        except Exception as exc:
            spread, percentile, pe, value_date, error = None, None, None, None, str(exc)
        indicators["hs300_equity_bond_spread"] = meta(method, source, url, fetched_at, error) | {
            "value": spread, "date": value_date, "hs300_equity_bond_spread": spread,
            "hs300_equity_bond_spread_percentile_10y": percentile,
            "hs300_equity_bond_spread_pe_ttm": pe, "hs300_equity_bond_spread_date": value_date,
            "hs300_equity_bond_spread_method": method, "hs300_equity_bond_spread_source": source,
            "hs300_equity_bond_spread_source_url": url, "hs300_equity_bond_spread_fetched_at": fetched_at,
        }
    finally:
        if owns_session:
            session.close()
    return {"schema_version": "1.0", "generated_at": fetched_at, "indicators": indicators}


def write_market_valuation_snapshot(snapshot, path=MARKET_VALUATION_OUTPUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary_path = handle.name
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return path


def run_market_valuation(path=MARKET_VALUATION_OUTPUT, session=None, now=None):
    snapshot = collect_market_valuation_snapshot(session=session, now=now)
    write_market_valuation_snapshot(snapshot, path)
    return snapshot


def main():
    run_market_valuation()
    print(f"已导出市场估值快照: {MARKET_VALUATION_OUTPUT}")


if __name__ == "__main__":
    main()
