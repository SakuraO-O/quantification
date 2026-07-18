"""Versioned corporate-disclosure ingestion contracts.

Provider-specific announcement parsers plug into these contracts.  The module
does not promote a parsed metric into a research conclusion; industry values
remain pending until an explicit confirmation writes ``confirmed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

from .assets import security_id
from .supabase_store import SupabaseStore, payload_hash


@dataclass(frozen=True)
class SourceDocument:
    source: str
    source_record_id: str
    title: str
    document_type: str
    announcement_date: date
    content: bytes
    report_period: date | None = None
    document_url: str | None = None


@dataclass(frozen=True)
class FinancialFact:
    report_period: date
    metric_code: str
    value: float | None
    unit: str
    period_type: str
    announcement_date: date


@dataclass(frozen=True)
class IndustryMetric:
    period: date
    metric_code: str
    value: float | None
    unit: str


class DisclosureAdapter(Protocol):
    provider: str

    def list_documents(self, asset: dict, since: date | None) -> Iterable[SourceDocument]: ...

    def parse_financial_facts(self, document: SourceDocument) -> Iterable[FinancialFact]: ...

    def parse_industry_metrics(self, document: SourceDocument) -> Iterable[IndustryMetric]: ...


class CorporateDisclosureWriter:
    """Persist source metadata and normalized pending facts, never source files."""

    def __init__(self, store: SupabaseStore):
        self.store = store

    def ingest(self, asset: dict, adapter: DisclosureAdapter, since: date | None = None) -> int:
        sid = security_id(asset["symbol"], asset["market"])
        written = 0
        for document in adapter.list_documents(asset, since):
            content_hash = payload_hash({"content": document.content.hex()})
            rows = self.store.upsert(
                "source_documents",
                {
                    "security_id": sid,
                    "source": document.source,
                    "source_record_id": document.source_record_id,
                    "title": document.title,
                    "document_type": document.document_type,
                    "report_period": document.report_period,
                    "announcement_date": document.announcement_date,
                    "document_url": document.document_url,
                    "content_hash": content_hash,
                },
                "source,source_record_id,content_hash",
            )
            source_document_id = rows[0]["source_document_id"]
            for fact in adapter.parse_financial_facts(document):
                current = self.store.select(
                    "financial_facts", filters={"security_id": f"eq.{sid}", "report_period": f"eq.{fact.report_period}", "metric_code": f"eq.{fact.metric_code}", "is_current": "eq.true"}, limit=1
                )
                if current and current[0].get("source_document_id") == source_document_id:
                    continue
                version = int(current[0]["version"]) + 1 if current else 1
                if current:
                    self.store.patch("financial_facts", {"financial_fact_id": f"eq.{current[0]['financial_fact_id']}"}, {"is_current": False})
                self.store.upsert(
                    "financial_facts",
                    {
                        "security_id": sid, "report_period": fact.report_period, "metric_code": fact.metric_code,
                        "value": fact.value, "unit": fact.unit, "period_type": fact.period_type,
                        "announcement_date": fact.announcement_date, "source_document_id": source_document_id,
                        "version": version, "is_current": True,
                    },
                    "security_id,report_period,metric_code,version",
                )
            for metric in adapter.parse_industry_metrics(document):
                existing = self.store.select(
                    "industry_metric_values", filters={"security_id": f"eq.{sid}", "period": f"eq.{metric.period}", "metric_code": f"eq.{metric.metric_code}"}, order="version.desc", limit=1
                )
                if existing and existing[0].get("source_document_id") == source_document_id:
                    continue
                version = int(existing[0]["version"]) + 1 if existing else 1
                self.store.upsert(
                    "industry_metric_values",
                    {
                        "security_id": sid, "period": metric.period, "metric_code": metric.metric_code,
                        "value": metric.value, "unit": metric.unit, "source_document_id": source_document_id,
                        "extraction_method": "automatic", "confirmation_status": "pending", "version": version,
                    },
                    "security_id,period,metric_code,version",
                )
            written += 1
        return written
