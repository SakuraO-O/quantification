"""Asset master-data helpers.

The source of truth for the first release is deliberately small and explicit:
12 indices and 9 stock holdings.  Database identities are deterministic so a
local backfill, CI run and Supabase migration all address the same security.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid5

from .config import ASSETS


SECURITY_NAMESPACE = UUID("4f494c20-a3bf-4f49-8335-f736fe097df9")


def security_id(symbol: str, market: str) -> str:
    """Return a stable UUID for a market/symbol pair."""

    return str(uuid5(SECURITY_NAMESPACE, f"{market}:{symbol}"))


def active_assets() -> list[dict]:
    """Return a detached copy so enrichment never mutates master data."""

    return deepcopy(ASSETS)


def asset_by_symbol(symbol: str) -> dict:
    for asset in ASSETS:
        if asset["symbol"] == symbol:
            return deepcopy(asset)
    raise KeyError(f"未在启用资产清单中找到代码: {symbol}")


def security_rows() -> list[dict]:
    """Rows suitable for the ``securities`` Supabase table."""

    return [
        {
            "security_id": security_id(asset["symbol"], asset["market"]),
            "symbol": asset["symbol"],
            "market": asset["market"],
            "name": asset["name"],
            "asset_type": asset["asset_type"],
            "currency": {"CN": "CNY", "HK": "HKD", "US": "USD"}[asset["market"]],
            "industry_template": asset.get("industry_template"),
            "is_active": True,
        }
        for asset in ASSETS
    ]


def provider_symbol_rows() -> list[dict]:
    """Return primary provider mappings, including optional fallback symbols."""

    rows: list[dict] = []
    for asset in ASSETS:
        sid = security_id(asset["symbol"], asset["market"])
        rows.append(
            {
                "security_id": sid,
                "provider": asset["provider"],
                "provider_symbol": asset["symbol"],
                "priority": 1,
                "is_active": True,
            }
        )
        for field, provider in (("eastmoney_symbol", "eastmoney"), ("yahoo_symbol", "yahoo"), ("nasdaq_symbol", "nasdaq")):
            if asset.get(field):
                rows.append(
                    {
                        "security_id": sid,
                        "provider": provider,
                        "provider_symbol": asset[field],
                        "priority": 2,
                        "is_active": True,
                    }
                )
    return rows
