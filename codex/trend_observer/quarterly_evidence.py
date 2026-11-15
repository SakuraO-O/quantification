"""Import human-reviewed figures from exact official disclosure files.

The manifest is a reviewed transcription, not a scraper's claim of accuracy.
Each source file is fetched and SHA-256 checked before any database write.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import re
from urllib.parse import urlparse

import requests

from .assets import security_id
from .quarterly import METRICS, _quarter
from .supabase_store import SupabaseStore


OFFICIAL_HOSTS = {
    "static.cninfo.com.cn", "www.cninfo.com.cn", "www.sse.com.cn",
    "www.szse.cn", "disc.static.szse.cn",
}
ROLES = {"proposal", "authorization", "approval", "implementation", "correction"}


def _checked_date(value: str) -> str:
    return date.fromisoformat(str(value)).isoformat()


def _checked_amount(value: object) -> str:
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("金额必须是有限数字")
    return str(amount)


def _fetch_document(url: str) -> bytes:
    response = requests.get(url, timeout=(5, 30))
    response.raise_for_status()
    if urlparse(response.url).hostname not in OFFICIAL_HOSTS:
        raise ValueError("正式公告链接重定向到了非官方站点")
    if len(response.content) > 30_000_000:
        raise ValueError("正式公告超过30MB，请单独核对")
    return response.content


def import_quarterly_evidence(store: SupabaseStore, manifest: dict, fetch_document=_fetch_document) -> dict:
    """Verify the whole manifest first, then idempotently persist reviewed facts."""
    symbol, market = str(manifest["symbol"]), str(manifest["market"])
    sid = security_id(symbol, market)
    reviewer = str(manifest["reviewed_by"]).strip()
    if not reviewer:
        raise ValueError("缺少核验人")
    checked_at = datetime.now(timezone.utc).isoformat()
    documents = {}
    for item in manifest.get("documents", []):
        key, url = str(item["key"]), str(item["url"])
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS:
            raise ValueError(f"非正式公告直达链接：{url}")
        digest = str(item["sha256"]).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"公告哈希格式错误：{key}")
        if hashlib.sha256(fetch_document(url)).hexdigest() != digest:
            raise ValueError(f"公告文件已变化或哈希不符：{key}")
        if key in documents:
            raise ValueError(f"重复公告键：{key}")
        documents[key] = {
            "security_id": sid, "source": parsed.hostname,
            "source_record_id": str(item["announcement_id"]),
            "title": str(item["title"]), "document_type": str(item["document_type"]),
            "report_period": _checked_date(item["report_period"]),
            "announcement_date": _checked_date(item["announcement_date"]),
            "document_url": url, "content_hash": digest,
        }
    facts = []
    for item in manifest.get("facts", []):
        metric, period = str(item["metric_code"]), _checked_date(item["report_period"])
        if metric not in METRICS or item["document"] not in documents:
            raise ValueError("财报指标或公告键无效")
        q = _quarter(period)
        if q is None or item["period_type"] != ("annual" if q == 4 else "year_to_date"):
            raise ValueError("财报报告期与累计/年度口径不一致")
        if documents[item["document"]]["report_period"] != period:
            raise ValueError("财报数值的报告期与正式公告不一致")
        facts.append({**item, "report_period": period, "value": _checked_amount(item["value"])})
    events = []
    for item in manifest.get("dividends", []):
        period = _checked_date(item["report_period"])
        refs = item["documents"]
        roles = {ref["role"] for ref in refs}
        if not refs or any(ref["key"] not in documents or ref["role"] not in ROLES for ref in refs):
            raise ValueError("分红公告依据不完整")
        stage = item["event_stage"]
        if stage not in ("proposal", "approved", "implemented"):
            raise ValueError("未知分红阶段")
        if not str(item.get("plan_key") or "").strip():
            raise ValueError("分红方案缺少稳定键")
        if not isinstance(item.get("supersedes_candidates"), list):
            raise ValueError("须列明本方案替代的候选公告编号；没有时填写空列表")
        per_share = Decimal(_checked_amount(item["cash_dividend_per_share"]))
        cash_total = Decimal(_checked_amount(item["cash_dividend_total"]))
        if per_share < 0 or cash_total < 0:
            raise ValueError("分红金额不能为负")
        if stage == "approved" and item.get("approval_basis") not in (
            "shareholder_resolution", "shareholder_authorized_board"
        ):
            raise ValueError("已批准方案缺少批准方式")
        if stage == "approved" and (
            "approval" not in roles or
            (item.get("approval_basis") == "shareholder_authorized_board" and "authorization" not in roles)
        ):
            raise ValueError("已批准方案缺少批准或授权依据")
        if stage == "implemented" and "implementation" not in roles:
            raise ValueError("已实施方案缺少实施公告")
        if stage == "implemented":
            _checked_date(item["ex_date"])
            for field in ("bonus_shares_per_share", "transfer_shares_per_share"):
                if Decimal(_checked_amount(item[field])) < 0:
                    raise ValueError("送转股比例不能为负")
        if int(item["fiscal_year"]) != date.fromisoformat(period).year:
            raise ValueError("分红所属年度与报告期不一致")
        events.append({**item, "report_period": period})
    reviews = []
    for item in manifest.get("year_reviews", []):
        if item["document"] not in documents or item["status"] not in ("pending", "authorization_only", "complete"):
            raise ValueError("年度分红核验依据无效")
        reviews.append(item)

    fact_keys = [(item["report_period"], item["metric_code"]) for item in facts]
    plan_keys = [str(item["plan_key"]) for item in events]
    if len(fact_keys) != len(set(fact_keys)) or len(plan_keys) != len(set(plan_keys)):
        raise ValueError("核验清单含重复的财报指标或分红方案")

    # Fetch histories once. A ten-year review can contain hundreds of facts;
    # one REST query per fact made a normal import unnecessarily slow.
    fact_history: dict[tuple[str, str], list[dict]] = {}
    for row in store.select("financial_facts",
                            select="financial_fact_id,report_period,metric_code,value,version,source_document_id,verification_status,is_current",
                            filters={"security_id": f"eq.{sid}"}, order="report_period.asc,metric_code.asc,version.asc"):
        fact_history.setdefault((str(row["report_period"]), row["metric_code"]), []).append(row)
    event_history: dict[str, list[dict]] = {}
    candidates: dict[str, list[dict]] = {}
    for row in store.select("dividend_events",
                            select="dividend_event_id,announcement_id,fiscal_year,plan_key,version,event_stage,report_period,approval_basis,cash_dividend_per_share,cash_dividend_total,source_document_id,ex_date,bonus_shares_per_share,transfer_shares_per_share,is_current,verification_status",
                            filters={"security_id": f"eq.{sid}"}, order="dividend_event_id.asc"):
        if row.get("plan_key"):
            event_history.setdefault(str(row["plan_key"]), []).append(row)
        if row.get("verification_status") == "candidate":
            candidates.setdefault(str(row["announcement_id"]), []).append(row)
    superseded = set()
    for item in events:
        for announcement_id in item["supersedes_candidates"]:
            rows = candidates.get(str(announcement_id), [])
            if not rows or any(int(row["fiscal_year"]) != int(item["fiscal_year"]) for row in rows):
                raise ValueError(f"待关联候选分红不存在或所属年度不符：{announcement_id}")
            superseded.add(str(announcement_id))
    for item in reviews:
        if item["status"] == "complete" and any(
            row.get("is_current") and str(row["announcement_id"]) not in superseded
            for rows in candidates.values() for row in rows
            if int(row["fiscal_year"]) == int(item["fiscal_year"])
        ):
            raise ValueError(f"{item['fiscal_year']}年仍有未关联的候选分红，不能标记核验完成")

    saved = store.upsert("source_documents", list(documents.values()), "source,source_record_id,content_hash")
    ids = {(row["source_record_id"], row["content_hash"]): row["source_document_id"] for row in saved}
    document_ids = {key: ids[(row["source_record_id"], row["content_hash"])] for key, row in documents.items()}
    written = {"facts": 0, "dividends": 0, "reviews": 0}
    for item in facts:
        history = fact_history.get((item["report_period"], item["metric_code"]), [])
        prior = next((row for row in history if row.get("is_current")), None)
        document_id = document_ids[item["document"]]
        if (prior and Decimal(str(prior["value"])) == Decimal(item["value"])
                and prior["source_document_id"] == document_id
                and prior.get("verification_status") == "verified"):
            continue
        created = store.insert("financial_facts", {
            "security_id": sid, "report_period": item["report_period"], "metric_code": item["metric_code"],
            "value": item["value"], "unit": METRICS[item["metric_code"]][1],
            "period_type": item["period_type"], "announcement_date": documents[item["document"]]["announcement_date"],
            "source_document_id": document_id, "version": max((int(row["version"]) for row in history), default=0) + 1,
            "verification_status": "verified", "verified_by": reviewer, "verified_at": checked_at,
            "is_current": False,
        })[0]
        try:
            if prior:
                store.patch("financial_facts", {"financial_fact_id": f"eq.{prior['financial_fact_id']}"}, {"is_current": False})
            store.patch("financial_facts", {"financial_fact_id": f"eq.{created['financial_fact_id']}"}, {"is_current": True})
        except Exception:
            if prior:
                store.patch("financial_facts", {"financial_fact_id": f"eq.{prior['financial_fact_id']}"}, {"is_current": True})
            raise
        written["facts"] += 1
    for item in events:
        key = str(item["plan_key"])
        def retire_candidates() -> None:
            for announcement_id in item["supersedes_candidates"]:
                store.patch("dividend_events",
                    {"security_id": f"eq.{sid}", "announcement_id": f"eq.{announcement_id}"},
                    {"is_current": False, "superseded_by_plan_key": key})
        history = event_history.get(key, [])
        prior = next((row for row in history if row.get("is_current")), None)
        refs = item["documents"]
        main_id = document_ids[refs[-1]["key"]]
        amount = _checked_amount(item["cash_dividend_per_share"])
        total = _checked_amount(item["cash_dividend_total"])
        if (prior and prior["event_stage"] == item["event_stage"]
                and Decimal(str(prior["cash_dividend_per_share"])) == Decimal(amount)
                and Decimal(str(prior["cash_dividend_total"])) == Decimal(total)
                and prior["report_period"] == item["report_period"]
                and prior.get("approval_basis") == item.get("approval_basis")
                and prior.get("ex_date") == item.get("ex_date")
                and Decimal(str(prior.get("bonus_shares_per_share") or 0)) == Decimal(str(item.get("bonus_shares_per_share") or 0))
                and Decimal(str(prior.get("transfer_shares_per_share") or 0)) == Decimal(str(item.get("transfer_shares_per_share") or 0))
                and prior["source_document_id"] == main_id):
            retire_candidates()
            continue
        created = store.insert("dividend_events", {
            "security_id": sid, "fiscal_year": int(item["fiscal_year"]), "report_period": item["report_period"],
            "plan_key": key, "version": max((int(row["version"]) for row in history), default=0) + 1,
            "event_stage": item["event_stage"], "announcement_id": documents[refs[-1]["key"]]["source_record_id"],
            "cash_dividend_per_share": amount, "cash_dividend_total": total,
            "approval_basis": item.get("approval_basis"), "ex_date": item.get("ex_date"),
            "bonus_shares_per_share": item.get("bonus_shares_per_share"),
            "transfer_shares_per_share": item.get("transfer_shares_per_share"),
            "announcement_date": documents[refs[-1]["key"]]["announcement_date"],
            "source_document_id": main_id, "verification_status": "verified",
            "verified_by": reviewer, "verified_at": checked_at, "is_current": False,
        })[0]
        store.insert("dividend_event_sources", [
            {"dividend_event_id": created["dividend_event_id"],
             "source_document_id": document_ids[ref["key"]], "evidence_role": ref["role"]}
            for ref in refs
        ])
        try:
            if prior:
                store.patch("dividend_events", {"dividend_event_id": f"eq.{prior['dividend_event_id']}"}, {"is_current": False})
            store.patch("dividend_events", {"dividend_event_id": f"eq.{created['dividend_event_id']}"}, {"is_current": True})
        except Exception:
            if prior:
                store.patch("dividend_events", {"dividend_event_id": f"eq.{prior['dividend_event_id']}"}, {"is_current": True})
            raise
        retire_candidates()
        written["dividends"] += 1
    for item in reviews:
        store.upsert("dividend_year_reviews", {
            "security_id": sid, "fiscal_year": int(item["fiscal_year"]), "status": item["status"],
            "source_document_id": document_ids[item["document"]],
            "reviewed_by": reviewer, "reviewed_at": checked_at,
        }, "security_id,fiscal_year")
        written["reviews"] += 1
    return written
