# 趋势观察台架构

## 目录结构

```text
quantification/
├── .github/workflows/daily_trend_observer.yml
├── README.md
└── codex/
    ├── dashboard_example.html
    ├── dashboard_data.json
    ├── data/trend_snapshot.json
    ├── dividends.json
    ├── history/
    ├── trend_observer.py
    └── trend_observer/
        ├── config.py
        ├── data_sources.py
        ├── dividends.py
        ├── analysis.py
        ├── pipeline.py
        ├── outputs.py
        ├── feishu.py
        └── cli.py
```

## 数据流

1. `data_sources.py` 从中证、国证、腾讯、东方财富抓取日线行情。
2. `analysis.py` 计算收益率、均线、斜率、短中长期趋势、综合状态、PE 百分位和估值状态。
3. `pipeline.py` 对资产清单执行一次完整抓取和计算，产出最新截面与历史序列。
4. `outputs.py` 导出统一快照 `codex/data/trend_snapshot.json`，并派生：
   - `codex/dashboard_data.json`
   - `codex/history/manifest.json`
   - `codex/history/{symbol}.json`
   - CSV 和 Markdown 报告
5. `feishu.py` 从统一快照生成飞书消息并发送。
6. `dashboard_example.html` 读取 `dashboard_data.json` 和 `history/` 展示看板。

## 文件职责

- `codex/trend_observer.py`：兼容旧命令的入口，转发到模块化 CLI。
- `codex/trend_observer/cli.py`：命令行入口，串联完整流程和飞书发送。
- `codex/trend_observer/config.py`：路径、常量、资产清单和报告字段。
- `codex/trend_observer/data_sources.py`：行情数据获取和数据源路由。
- `codex/trend_observer/dividends.py`：股票上一年每股分红配置和股息率计算。
- `codex/trend_observer/analysis.py`：指标、趋势、估值和信号计算。
- `codex/trend_observer/pipeline.py`：一次性抓取与分析，避免看板或飞书重复抓取。
- `codex/trend_observer/outputs.py`：统一 JSON、看板 JSON、历史 JSON、报告导出。
- `codex/trend_observer/feishu.py`：飞书消息模板和 webhook 发送。

## GitHub Actions

`.github/workflows/daily_trend_observer.yml` 每日运行：

```bash
python -m codex.trend_observer.cli --notify
```

工作流会提交 `codex/data/trend_snapshot.json`、`codex/dashboard_data.json` 和 `codex/history/**`，并将看板发布到 GitHub Pages。

