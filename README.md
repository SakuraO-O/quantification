# 趋势观察台

指数和股票趋势观察工具，用于生成看板数据、历史行情、Markdown/CSV 报告和飞书通知。

本项目只用于趋势观察，不构成投资建议。

## 安装依赖

```bash
python -m pip install -r codex/requirements.txt
```

## 运行完整流程

```bash
python -m codex.trend_observer.cli
```

兼容旧入口：

```bash
python codex/trend_observer.py
```

运行后会生成：

- `codex/data/trend_snapshot.json`
- `codex/dashboard_data.json`
- `codex/history/manifest.json`
- `codex/history/{symbol}.json`
- `codex/trend_observer_report.csv`
- `codex/trend_observer_report.md`
- `codex/trend_history.csv`
- `codex/trend_history.json`

## 发送飞书通知

先配置环境变量：

```bash
export FEISHU_WEBHOOK_URL="你的飞书机器人 webhook"
export FEISHU_SECRET="可选的签名密钥"
```

然后运行：

```bash
python -m codex.trend_observer.cli --notify
```

飞书消息从统一快照 `codex/data/trend_snapshot.json` 派生，和看板使用同一批抓取与计算结果。

## 本地查看看板

可直接打开：

```text
codex/dashboard_example.html
```

也可以启动本地静态服务：

```bash
python -m http.server 8766
```

然后访问：

```text
http://127.0.0.1:8766/codex/dashboard_example.html
```

## 运行测试

```bash
python -m unittest discover -s tests
```

## GitHub Actions

`.github/workflows/daily_trend_observer.yml` 会按计划执行：

```bash
python -m codex.trend_observer.cli --notify
```

工作流会提交最新的统一快照、看板截面数据和拆分历史数据，并发布 GitHub Pages。

## 架构说明

详见 `ARCHITECTURE.md`。

