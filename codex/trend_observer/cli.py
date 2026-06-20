"""Command line entrypoint for the trend observer."""

import argparse

from .config import CSV_OUTPUT, DASHBOARD_OUTPUT, HISTORY_CSV_OUTPUT, HISTORY_DIR, HISTORY_JSON_OUTPUT, MARKDOWN_OUTPUT, SNAPSHOT_OUTPUT
from .feishu import post_feishu_message
from .outputs import export_all, format_markdown
from .pipeline import build_results


def parse_args():
    parser = argparse.ArgumentParser(description="生成三周期均线趋势观察报告。")
    parser.add_argument("--notify", action="store_true", help="生成报告后发送飞书摘要通知。")
    return parser.parse_args()


def run(notify=False):
    results, history_output = build_results()
    snapshot = export_all(results, history_output)
    print(f"\n已导出 CSV: {CSV_OUTPUT}")
    print(f"已导出 Markdown: {MARKDOWN_OUTPUT}")
    print(f"已导出统一快照 JSON: {SNAPSHOT_OUTPUT}")
    print(f"已导出网页数据: {DASHBOARD_OUTPUT}")
    print(f"已导出历史 JSON: {HISTORY_JSON_OUTPUT}")
    print(f"已导出拆分历史目录: {HISTORY_DIR}")
    print(f"已导出历史 CSV: {HISTORY_CSV_OUTPUT}\n")
    print(format_markdown(results))
    if notify:
        post_feishu_message(snapshot)


def main():
    try:
        run(parse_args().notify)
    except KeyboardInterrupt:
        print("\n已取消趋势观察。", flush=True)


if __name__ == "__main__":
    main()

