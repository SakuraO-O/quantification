"""Quarterly verification rules and the local, serial Wind worker.

This is the only automatic comparison implementation. A reference is useful
only when its identity, basis and revision semantics are actually supplied;
missing provider metadata becomes a review item, never an inferred match.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from .supabase_store import SupabaseStore, payload_hash

RULE_VERSION = "quarterly-crosscheck-v1"
UNITS = {"元": ("CNY", Decimal(1)), "万元": ("CNY", Decimal(10000)),
         "亿元": ("CNY", Decimal(100000000)), "百万元": ("CNY", Decimal(1000000)),
         "%": ("percent", Decimal(1)), "元/股": ("CNY/share", Decimal(1))}
LABELS = {"扣非归母净利润": "deducted_net_profit", "归母净利润": "net_profit",
          "经营活动产生的现金流量净额": "operating_cashflow", "营业总收入": "revenue",
          "营业收入": "revenue", "加权平均净资产收益率": "roe", "资产负债率": "asset_liability_ratio"}
QUARTERS = {"03-31": "第一季度", "06-30": "半年度", "09-30": "前三季度", "12-31": "年度"}


def decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def compare_reference(item: dict, reference: dict | None) -> dict:
    def result(status, reason):
        return {"status": status, "reason": reason, "reference": reference, "rule_version": RULE_VERSION}
    if not reference or decimal(reference.get("value")) is None:
        return result("failed", "未取得同报告期的有效参考值；空值不是0")
    if not item.get("metadata_complete"):
        return result("basis", "候选来源口径不完整，请先补齐")
    if "wind" in str(item.get("source", "")).lower() or not item.get("source"):
        return result("basis", "候选来源不明或与Wind同源，不能重复交叉核验")
    expected_code = item["symbol"][-6:] + (".SH" if item["symbol"].startswith("sh") else ".SZ")
    for key, expected in (("wind_code", expected_code), ("report_period", item["report_period"]),
                          ("unit", item["unit"]),
                          ("period_type", item["period_type"])):
        if reference.get(key) != expected:
            return result("basis", f"参考{key}缺失或与候选口径不一致")
    if item["unit"] != "percent" and reference.get("currency") != "CNY":
        return result("basis", "参考币种缺失或不一致")
    if reference.get("revision_basis") not in ("latest_available", "original_report"):
        return result("basis", "参考回执未明确原报告/最新调整口径，需补充依据")
    candidate_revision = (item.get("review_data") or {}).get("revision_basis")
    if candidate_revision and candidate_revision != reference["revision_basis"]:
        return result("basis", "候选与参考采用不同修订口径")
    if reference.get("known_conflict"):
        return result("diff", "参考存在已知更正或来源冲突，须查证")
    if item["kind"] == "dividend":
        fields = item["fields"]
        for field in ("plan_key", "event_stage", "ex_date", "approval_basis"):
            if fields.get(field) != reference.get(field):
                return result("basis", f"分红{field}不一致或未明确")
        if reference.get("fiscal_year") != item.get("fiscal_year"):
            return result("basis", "分红所属年度不一致")
        for field in ("bonus_shares_per_share", "transfer_shares_per_share", "cash_dividend_total"):
            if decimal(fields.get(field)) is None or decimal(fields.get(field)) != decimal(reference.get(field)):
                return result("diff", f"分红{field}缺少可靠值或不一致")
    candidate = decimal(item.get("value"))
    if candidate is None:
        return result("basis", "候选数值缺失")
    difference = candidate - decimal(reference["value"])
    reference = dict(reference, difference=str(difference), difference_ratio=(
        str(difference / abs(decimal(reference["value"])) * 100) if decimal(reference["value"]) else None))
    if not difference:
        return result("auto", "来源及统计口径一致，数值相等；保留候选原精度")
    tolerance = decimal(reference.get("tolerance"))
    if tolerance is not None and tolerance >= 0 and reference.get("precision_evidence") and abs(difference) <= tolerance:
        return result("auto", "仅来源展示精度舍入差；保留候选原精度")
    return result("diff", "候选与参考存在实质数值差异")


def _tables(receipt: dict) -> list[dict]:
    if receipt.get("ok") is False or receipt.get("isError"):
        raise RuntimeError("Wind返回错误：" + str(receipt.get("code") or "backend_error"))
    content = receipt.get("content") or []
    if not content:
        raise ValueError("Wind回执没有结构化内容")
    body = json.loads(content[0]["text"], parse_float=Decimal)
    return body.get("data", {}).get("data", [])


def parse_wind_receipt(receipt: dict, item: dict) -> dict | None:
    """Read metadata from returned columns/cells, never from the request alone."""
    matches = []
    for table in _tables(receipt):
        columns = table.get("columns", [])
        for row in table.get("rows", []):
            cells = {c["name"]: value for c, value in zip(columns, row)}
            code = cells.get("Wind代码")
            for column, value in zip(columns, row):
                label = column["name"]
                metric = next((metric for name, metric in LABELS.items() if name in label), None)
                if metric != item["metric_code"]:
                    continue
                year = re.search(r"(20\d{2}|19\d{2})", label)
                if not year:
                    continue
                if any(x in label for x in ("前三季", "三季度", "第三季度")):
                    suffix, basis = "09-30", "year_to_date" if "单季" not in label else "single_quarter"
                elif any(x in label for x in ("半年度", "半年", "上半年", "中报", "1-6月")):
                    suffix, basis = "06-30", "year_to_date"
                elif any(x in label for x in ("一季度", "第一季度", "1季报")):
                    suffix, basis = "03-31", "year_to_date"
                elif any(x in label for x in ("年报", "年度", "全年")):
                    suffix, basis = "12-31", "annual"
                else:
                    continue
                if "单季" in label or "本季度" in label:
                    basis = "single_quarter"
                period = f"{year[0]}-{suffix}"
                if period != item["report_period"]:
                    continue
                unit = UNITS.get(column.get("unit"))
                amount = decimal(value)
                if not unit or amount is None:
                    continue
                # Revision information is not guaranteed by this NL tool. Ask
                # for it, use it when present, and leave uncertainty visible.
                revision_text = str(column.get("revision_basis") or cells.get("修订口径") or table.get("revision_basis") or "")
                revision = ("latest_available" if any(x in revision_text for x in ("latest_available", "最新", "调整后"))
                            else "original_report" if any(x in revision_text for x in ("original_report", "原报告", "未调整")) else None)
                precision = max(0, -amount.as_tuple().exponent)
                matches.append({"wind_code": code, "report_period": period, "period_type": basis,
                    "unit": unit[0], "currency": column.get("currency") or cells.get("币种"),
                    "value": str(amount * unit[1]), "tolerance": str(Decimal(5).scaleb(-precision - 1) * unit[1]),
                    "precision_evidence": {"display_value": str(value), "unit": column["unit"]},
                    "revision_basis": revision, "source": "Wind", "column": label})
    if len(matches) > 1:
        raise ValueError("Wind同指标同报告期存在多条值，不能任选其一")
    return matches[0] if matches else None


class WindReference:
    def __init__(self, cli: Path, cache: Path, *, runner=subprocess.run, now=time.time):
        self.cli, self.cache, self.runner, self.now = cli, cache, runner, now

    def __call__(self, item: dict) -> dict | None:
        code = item["symbol"][-6:] + (".SH" if item["symbol"].startswith("sh") else ".SZ")
        # Bind cache to source version AND freshness. A user retry cannot keep
        # reading an old period file forever, as the original offline script did.
        key = payload_hash({"symbol": code, "period": item["report_period"], "hash": item.get("content_hash") or item["fact_hash"],
                            "kind": item["kind"], "attempt": item.get("attempt_seq", 1), "rule": RULE_VERSION})
        path = self.cache / (key + ".json")
        if item["kind"] == "dividend":
            question = (f"查询{code}所属报告期{item['report_period']}的现金分红方案；包含报告年度、实施或批准阶段、"
                        "税前人民币每股金额、分红总额、除息日、送转比例及明确修订口径；缺失字段请明确返回空值。")
            tool = "get_stock_events"
        else:
            question = (f"查询{code}在{item['report_period'][:4]}年{QUARTERS.get(item['report_period'][5:], '')}报告期的"
                        "归母净利润、扣非归母净利润、营业收入、经营活动产生的现金流量净额、加权平均净资产收益率、资产负债率；"
                        "标明证券代码、每列原始单位及币种、累计或期末口径，并独立返回修订口径为原报告或最新调整后；"
                        "仅返回该报告期。不能确定的元数据请保留为空。")
            tool = "get_stock_fundamentals"
        self.cache.mkdir(parents=True, exist_ok=True)
        if path.exists() and self.now() - path.stat().st_mtime < 900:
            receipt = json.loads(path.read_text())
        else:
            process = self.runner(["node", str(self.cli), "call", "stock_data", tool,
                                  json.dumps({"question": question}, ensure_ascii=False)],
                                 capture_output=True, text=True, timeout=150, cwd=self.cli.parent.parent)
            if process.returncode:
                raise RuntimeError("Wind CLI执行失败；请检查本地授权或网络，未提升核验状态")
            receipt = json.loads(process.stdout)
            _tables(receipt)  # Error envelopes must never poison the cache.
            path.write_text(json.dumps(receipt, ensure_ascii=False))
        if item["kind"] == "dividend":
            reference = parse_wind_dividend(receipt, item)
        else:
            reference = parse_wind_receipt(receipt, item)
        if reference:
            reference["receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            reference["fetched_at"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        return reference


def parse_wind_dividend(receipt: dict, item: dict) -> dict | None:
    """Require an unambiguous event, including its declared reporting year."""
    found = []
    suffixes = {"report_period": "分红报告期", "fiscal_year": "分红所属报告年度", "event_stage": "分红方案进度",
                "ex_date": "分红除权除息日", "value": "分红税前每股派息", "cash_dividend_total": "分红现金总额",
                "bonus_shares_per_share": "分红送股比例", "transfer_shares_per_share": "分红转增比例",
                "revision_basis": "修订口径", "approval_basis": "分红批准依据", "plan_key": "分红方案标识"}
    for table in _tables(receipt):
        for row in table.get("rows", []):
            cells = {c["name"]: value for c, value in zip(table["columns"], row)}
            def get(suffix):
                matches = [value for key, value in cells.items() if key.endswith(suffix)]
                return matches[0] if len(matches) == 1 else None
            values = {name: get(label) for name, label in suffixes.items()}
            if values["report_period"] != item["report_period"]:
                continue
            fields = item["fields"]
            if values["ex_date"] != fields.get("ex_date"):
                continue
            values.update(wind_code=cells.get("Wind代码"), period_type="event", source="Wind")
            amount_col = next((c for c in table["columns"] if c["name"].endswith(suffixes["value"])), {})
            # Unitless and pre-/after-tax ambiguous dividends are not matches.
            values["unit"] = "CNY/share" if amount_col.get("unit") == "元/股" else None
            values["currency"] = amount_col.get("currency")
            values["event_stage"] = {"已实施": "implemented", "实施完毕": "implemented", "已批准": "approved"}.get(values["event_stage"], values["event_stage"])
            try:
                values["fiscal_year"] = int(values["fiscal_year"])
            except (ValueError, TypeError):
                values["fiscal_year"] = None
            for field in ("value", "cash_dividend_total", "bonus_shares_per_share", "transfer_shares_per_share"):
                if isinstance(values[field], Decimal):
                    values[field] = str(values[field])
            found.append(values)
    if len(found) > 1:
        raise ValueError("Wind分红方案不能唯一匹配")
    return found[0] if found else None


def run_verification_worker(store, provider, *, limit=20) -> int:
    store.rpc("review_event", {"p_type": "heartbeat", "p_key": "local-wind", "p_security": None,
              "p_actor": "local-worker", "p_data": {"state": "online"}})
    count = 0
    for _ in range(limit):
        store.rpc("review_event", {"p_type": "heartbeat", "p_key": "local-wind", "p_security": None,
                  "p_actor": "local-worker", "p_data": {"state": "online"}})
        job = store.rpc("review_claim", {"p_kind": "verification"})
        if not job:
            break
        try:
            result = compare_reference(job["item"], provider(dict(job["item"], attempt_seq=job["attempt_seq"])))
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            result = {"status": "failed", "reason": str(exc)[:1000], "rule_version": RULE_VERSION}
        store.rpc("review_complete", {"p_job": job["job_id"], "p_attempt": job["attempt_seq"],
                  "p_lease": job["lease_token"], "p_result": result})
        count += 1
        if result["status"] == "failed":
            if getattr(provider, "pause_on_failure", False):
                raise RuntimeError("Wind取数失败，执行器已暂停；修复授权或网络后重新启动")
            break  # Do not expand an auth/quota/network outage to other stocks.
    return count


def run_publish_request(store, job_id: str, attempt: int, publisher) -> dict:
    job = store.rpc("review_claim", {"p_kind": "publish", "p_job": job_id, "p_attempt": attempt})
    if not job:
        raise RuntimeError("发布请求已结束、过期或被新尝试替代")
    try:
        version = publisher.publish()
        if not version.get("is_complete", False):
            raise RuntimeError("看板版本不完整，未完成发布")
        result = {"status": "succeeded", "version": version["dashboard_version_id"], "run_id": os.getenv("GITHUB_RUN_ID")}
    except Exception as exc:
        store.rpc("review_complete", {"p_job": job_id, "p_attempt": job["attempt_seq"],
                  "p_lease": job["lease_token"], "p_result": {"status": "failed", "reason": str(exc)[:1000]}})
        raise
    return store.rpc("review_complete", {"p_job": job_id, "p_attempt": job["attempt_seq"],
                     "p_lease": job["lease_token"], "p_result": result})


def main():
    parser = argparse.ArgumentParser(description="本地Wind季度核实执行器；只处理线上明确排队的候选任务")
    parser.add_argument("--wind-cli", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/trend-verification")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    store = SupabaseStore.from_env()
    if not store:
        parser.error("需要现有SUPABASE_URL及服务端凭据，不能使用浏览器公开密钥")
    if args.interval < 10 or args.limit < 1:
        parser.error("interval至少10秒，limit至少1")
    if not args.wind_cli.is_file():
        parser.error("Wind CLI路径不存在")
    provider = WindReference(args.wind_cli.resolve(), args.cache)
    provider.pause_on_failure = True
    while True:
        try:
            print(f"已处理 {run_verification_worker(store, provider, limit=args.limit)} 项核实任务", flush=True)
        except Exception:
            if not args.watch:
                raise
            try:
                store.rpc("review_event", {"p_type": "heartbeat", "p_key": "local-wind", "p_security": None, "p_actor": "local-worker", "p_data": {"state": "failed", "reason": "执行失败，需检查后重启"}})
            except Exception:
                pass
            print("执行器已暂停；请检查本地授权、网络或任务回执后重新启动。未核实数据保持待处理。", flush=True)
            raise
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
