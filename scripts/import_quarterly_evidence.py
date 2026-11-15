"""Import one reviewed disclosure manifest without storing report files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex.trend_observer.quarterly_evidence import import_quarterly_evidence
from codex.trend_observer.supabase_store import SupabaseStore


def main() -> None:
    parser = argparse.ArgumentParser(description="核验并导入正式财报、分红公告摘录")
    parser.add_argument("manifest", type=Path, help="经人工逐项核对的 JSON 文件")
    args = parser.parse_args()
    store = SupabaseStore.from_env()
    if store is None:
        parser.error("请配置 SUPABASE_URL 和 SUPABASE_SECRET_KEY")
    result = import_quarterly_evidence(store, json.loads(args.manifest.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
