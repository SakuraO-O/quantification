# 趋势观察台

指数和股票趋势观察工具。V2 将行情、估值、配置和派生信号写入 Supabase，并由受控 Edge API 提供给看板；旧 JSON 导出暂时保留，用于迁移核对与回退。

本项目只用于趋势观察，不构成投资建议。

## 安装依赖

```bash
python -m pip install -r codex/requirements.txt
```

## V2：Supabase 数据管道

先创建 Supabase 项目并在本机或 GitHub Actions 中配置**服务端**环境变量（不得写入前端或仓库）：

```bash
export SUPABASE_URL="https://<project-ref>.supabase.co"
export SUPABASE_SECRET_KEY="<server-secret-key>"
```

首次部署数据库时，安装 Supabase CLI 后执行：

```bash
supabase login
supabase link --project-ref calebaqhmihatnssjgon
supabase db push --dry-run
supabase db push
supabase functions deploy dashboard-api
supabase functions deploy portfolio-config
```

托管的 Edge Function 会自动获得 `SUPABASE_PUBLISHABLE_KEYS` 和 `SUPABASE_SECRET_KEYS`，无需手工复制密钥。只需额外设置
`DASHBOARD_ALLOWED_ORIGINS=https://sakurao-o.github.io,http://127.0.0.1:8766`。函数自行校验 Supabase Auth，因此服务端密钥不会下发到浏览器。

本项目采用单用户模式：`supabase/config.toml` 已关闭邮箱公开注册。数据库和函数部署后，在 Supabase Dashboard 的 Authentication → Users 中由管理员创建唯一用户，再把该用户的 **App Metadata** 设置为 `{"role":"editor"}`。不要把角色写入可由用户修改的 User Metadata。`viewer` 仅可读取看板，`editor` 才可保存资产配置。

接着初始化资产主数据、运行增量同步、发布看板版本：

```bash
python -B -m codex.trend_observer.cli bootstrap
python -B -m codex.trend_observer.cli sync-market --market CN
python -B -m codex.trend_observer.cli sync-valuation --market CN
python -B -m codex.trend_observer.cli publish-dashboard
```

启用资产清单固定为 **12 个指数和当前 9 只股票**。`sync-market` 仅同步行情；`sync-valuation` 独立同步指数估值。二者都会读取数据水位，正常情况下只请求新增日期及向前重叠的两个交易日；`--force` 仅用于补抓或修订检查。

飞书派发只读取完整的 `dashboard_versions`，周一至周六在 08:00—09:30 每 10 分钟检查；同一内容版本只成功派发一次，周日不发送：

```bash
python -B -m codex.trend_observer.cli dispatch-feishu
```

V2 自动任务位于 [.github/workflows/trend_observer_v2.yml](.github/workflows/trend_observer_v2.yml)。07:30 的港股、海外行情、估值与股票基本面同步完成后会立即发布看板版本；08:00—09:30 窗口负责检查版本并派发飞书。未配置 Supabase 密钥时会安全跳过，不会回退成全量抓取或发送旧通知。

### 上线检查清单

一次包含数据库或 Edge 变更的上线，按下面顺序完成：

1. 将包含新计算版本的代码合入 `main`；派生信号版本不一致时，发布器会把对应资产标为“信号版本待重算”，不会继续发布旧结论；
2. 在仓库根目录执行 `supabase db push`，使事实表、数据修复、枚举迁移和 `compute_portfolio_allocation()` 配置计算函数进入正式项目；
3. 部署发生变更的函数（通常为 `supabase functions deploy dashboard-api` 与 `supabase functions deploy portfolio-config`），并等待 Pages 部署静态资源；
4. 对修改过行情、估值或计算规则的版本，依次强制运行受影响市场的 `sync-valuation`、`sync-market`，确认所有资产的 `calculation_version` 已更新后才运行 `publish-dashboard`；
5. 首次或修复基本面后，手动运行 `sync-fundamentals`（需要补抓时选择 `--force`），随后运行 `publish-dashboard`；
6. 验证 `fundamental_assessments`、`financial_facts` 与最新 `dashboard_versions.payload` 均有 9 只股票的有效数据，并在浏览器确认 `/overview` 的 `allocation` 与 `/asset/{symbol}` 的基本面字段正常返回。

配置偏离、理论调整额及摘要由数据库 RPC 实时计算；若迁移尚未部署，Edge API 不应以浏览器计算或旧版本快照代替该结果。

## 迁移期：兼容 JSON 导出

```bash
python -B -m codex.trend_observer.cli legacy-export
```

兼容旧单体入口（仅人工核对，不得配置为定时任务）：

```bash
python codex/trend_observer.py
```

运行后会生成：

- `codex/data/trend_snapshot.json`
- `codex/data/market_valuation_snapshot.json`
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
python -B -m codex.trend_observer.cli legacy-export --notify
```

这条命令仅用于迁移期核对。它的飞书消息从统一快照 `codex/data/trend_snapshot.json` 派生；正式 V2 推送必须使用 `dispatch-feishu`。
市场估值与利率数据单独保存，不进入看板、飞书消息或趋势判断。

## 单独抓取市场估值与利率

```bash
python -m codex.trend_observer.market_valuation
```

该命令写入 `codex/data/market_valuation_snapshot.json`。每个指标独立记录来源、来源网址、数据日期、抓取时间和错误信息；指定来源不可用时保存空值，不会使用其他来源替代。

## 本地查看看板

未配置 Supabase 时可直接打开，本地会使用内嵌的 12 个指数与 9 只股票 Mock 数据：

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

本地页面和 GitHub Pages 使用同一套登录界面。项目 Ref、API 地址和公开的
`Publishable key`（`sb_publishable_...`）已经写入 `codex/dashboard_config.js`，
登录页只需填写已创建用户的邮箱、密码。用户 access token 和 refresh token 仅保存在
当前标签页的 `sessionStorage`。不要把 secret key 或 `service_role` key 填入页面或
提交到仓库。

看板只通过受控 Edge API 读取数据；资产配置编辑会通过 editor 专用 API 原子写入 Supabase，并在重新加载时读取最新版本。详情弹窗打开时才按需请求历史行情、结构化财务指标、分红事件、行业指标及来源元数据。API 不可用或认证缺失时，页面会明确标记并回退到 Mock，不会读取旧版 `dashboard_data.json`。

## 运行测试

```bash
python -m unittest discover -s tests
```

## GitHub Actions

`.github/workflows/trend_observer_v2.yml` 按数据类型的预定时间执行增量同步，并在周一至周六早间发布看板版本和推送飞书。

`.github/workflows/daily_trend_observer.yml` 现仅可手动触发，用于迁移期生成 JSON 与 GitHub Pages 回退数据：

```bash
python -B -m codex.trend_observer.cli legacy-export
```

工作流会提交最新的统一快照、看板截面数据和拆分历史数据，并发布 GitHub Pages。

## 架构说明

详见 `ARCHITECTURE.md`。
