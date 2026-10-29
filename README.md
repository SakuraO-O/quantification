# 趋势观察台

指数和股票趋势观察工具。行情、估值、配置和派生信号写入 Supabase，由受控 Edge API 提供给看板。

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
python -B -m codex.trend_observer.cli sync-fundamentals --force
python -B -m codex.trend_observer.cli publish-dashboard
```

启用资产清单当前为 **12 个指数和 14 只股票**，以 `config.py` 为权威名单；看板读取发布版本中的资产清单，不另行写死股票数量。`sync-market` 仅同步行情；`sync-valuation` 独立同步指数估值。二者都会读取数据水位，正常情况下只请求新增日期及向前重叠的两个交易日；`--force` 仅用于补抓或修订检查。

### 股票股息率与上一年度每股分红

生产口径由 Python `DashboardPublisher` 统一计算：以资产交易日所在自然年的**前一报告年度**为统计年度（例如 2026-10-12 使用 2025 年报告期），按来源明确给出的 `report_period` 归属年度，汇总该年度所有截至交易日已实施、已除息的税前每股现金分红，包含季度、中期和年终方案。`last_year_dividend` 是这笔合计，`dividend_yield` 为合计 ÷ 当日收盘价 × 100%，保留两位小数；页面每股分红金额固定显示四位小数，计算时仍保留来源精度，不使用页面显示值反算股息率。尚未实施或除息日未到的方案不计入；报告期、除息日、分红金额或来源同步缺失时不猜测年度，显示数据不足。年内尚未实施的方案可能使上一报告年度合计随确认进度变化，发布值是截至当前交易日的已实施金额。

分红事实使用带明确“报告期”的东方财富分红送配详情，经 `sync-fundamentals` 写入 `dividend_events.report_period`。来源更正事件时，旧记录保留追溯但标记 `is_current=false`，不重复计入；任一报告期返回的已实施事件数少于已核验历史时同步失败，待人工核查。历史新浪记录的 `fiscal_year` 曾由除息年份减一推断，无法可靠覆盖季度和中期分红；迁移不会把这些旧记录当成已核验事实。发布器、三年含息回报与资产详情只消费已回填且当前有效的事件。`asset_daily_signals.dividend_yield` 不作为看板股息率依据；Edge、前端和飞书读取发布版本，不重算该指标。

本规则上线需先部署 `20261013120000_dividend_report_period.sql`，再部署更新后的 `dashboard-api`，执行 `sync-fundamentals --force` 回填当前启用的股票，检查报告期与各阶段分红，运行 `publish-dashboard`，最后发布静态看板。以同一版本核对 `last_year_dividend`、`dividend_fiscal_year`、`dividend_yield` 与页面显示；若历史事件尚未回填，该股票应保持缺数。修改代码或 Pages 本身不会补齐生产事实。

飞书派发只读取完整的 `dashboard_versions`，周一至周六在 08:00—09:30 每 10 分钟检查；同一内容版本只成功派发一次，周日不发送：

```bash
python -B -m codex.trend_observer.cli dispatch-feishu
```

V2 自动任务位于 [.github/workflows/trend_observer_v2.yml](.github/workflows/trend_observer_v2.yml)。北京时间18:45抓取国内行情；20:30先补抓落后行情，再独立抓取国内估值；次日07:30再次补抓国内行情，并执行港美行情、估值、股票基本面及看板发布。08:00—09:30窗口负责发布与派发飞书。同步命令失败后仍继续其他同步及发布，最后以非零状态汇总失败；派发窗口发布失败时不发送旧版本。未配置 Supabase 密钥时安全跳过。GitHub schedule不保证准点，以上是计划时间，需核查实际启动时间。

### 行情新鲜度与本地修复（v2.2）

- `freshness.py` 是应有行情日期的唯一计算模块：按市场时区、常规收盘时间和数据库交易日历确定最后完整交易日；缺日历时仅回退工作日，不能保证覆盖节假日。提前收市暂保守等待常规收盘时间。
- 主源返回非空旧数据也要尝试备用源；所有来源落后则明确失败，不把旧日期标记为更新成功。盘中／未来日期不写入日线。已达到应有日期且信号版本一致的资产跳过，休市日仍允许补齐上一交易日缺口。
- 当前PE缺失时百分位也必须缺失，即使有历史样本或覆盖值；历史有效估值保留。v2.2计算版本会重建历史信号，而不是只重算新到达的行情。
- 即使估值来源未变化，也会重试基于已有事实的信号计算，防止“事实写入成功、计算失败”永久遗漏。日志写入失败不会阻断后续资产，但会在命令最终结果中报告失败；正常批次记录应有日期、来源日期及备用源诊断。
- 中证系列估值现由独立 `sync-valuation` 调用中证官方 `index-perf` 日频接口，记录 `official_rolling_pe_daily` 口径，不再依赖行情同步附带写入PE。适配器请求支持的历史区间以补齐此前未接入期间的缺口；仅保留有限正数，缺值不填充。2026-09-15只读核验沪深300：2026-09-10为13.62、09-11为13.52。接口字段映射参考 [AKShare中证日频接口](https://github.com/akfamily/akshare/blob/main/akshare/index/index_stock_zh_csindex.py)。生产补回数量以部署后返回的有效记录为准。
- 创业板100（`sz399006`）的日线行情继续由腾讯增量更新；PE 只读取 Supabase 中由用户手动导入的 Wind 日频记录，定时 `sync-valuation` 对该指数直接跳过，不再请求国证当前 PE，也不覆盖手工数据。首次导入覆盖了 2010-06-01 至 2026-10-26 的 3,981 个交易日 PE，并将 PB、股息率百分数保存在独立的估值事实类型中；历史缺失的收盘价由 Wind 补足，已有腾讯行情保留。Wind 文件的历史“市盈率”列未明确标注 TTM，当前数据点曾与 Wind PE(TTM) 快照核对，历史口径仍待来源进一步确认。
- 创业板100的 PE 百分位使用现有近 2,520 个行情交易日窗口内的有效 PE 样本，至少 252 个样本才显示；仅在 PE 与行情同一交易日匹配时显示当日 PE 和百分位，缺失时不沿用上次值。手动补传 PE 后，运行 `rebuild-gem100` 从云端事实重算该指数信号并发布看板，无需再次读取本地 CSV；以后只补传新增或更正的 PE 即可。
- 国证自由现金流（`980092`）和恒生科技（`hkHSTECH`）的行情分别由国证指数、腾讯日线增量更新；定时估值同步跳过两者。PE、PB、股息率由用户按日期手动导入 Supabase，已有 PE 用手工 Wind 数据覆盖。行情和 PE 只按同一交易日匹配，缺失日不向前填充。PE 百分位沿用近 2,520 个行情交易日内至少 252 个有效 PE 样本的规则，历史标签按每个交易日实际可用区间计算。手动导入更正后运行 `rebuild-manual-index --symbol 980092` 或 `rebuild-manual-index --symbol hkHSTECH`，从云端事实重算并发布，无需读取本地文件。
- 恒生指数（`hkHSI`）的腾讯行情继续自动更新；Wind 历史收盘价只补齐云端缺失日期，已有腾讯行情保留。PE、PB、股息率从本地文件手动导入 Supabase，定时估值同步跳过该指数。导入范围为 2015-01-02 至 2026-10-28 共 2,908 个交易日；其中部分早期季度的 Wind 返回列名简写为“市盈率、市净率、股息率”，请求虽指定 TTM/PB_LF，但回执未逐日明确标注口径，详见本地说明。PE 百分位使用近 2,520 个行情交易日内至少 252 个有效 PE 样本，当日 PE 缺失时不沿用前值；历史标签按每个交易日实际样本跨度计算。手动更正后运行 `rebuild-manual-index --symbol hkHSI` 从云端事实重算并发布。
- 本次本地代码修改不等于已上线。后续需提交合并部署工作流，在受影响市场执行估值同步、行情重算后重新发布；不要修改历史晨报快照。生产补数需另外执行并验证。

### 股票近三年含息总回报（待部署）

股票观察列表在“近3年”后新增“近3年含息总回报”。前者为不含现金分红、计入送转股的价格回报；后者为税前现金分红在除权除息日再投入的累计回报，均采用三年前同日或之前最近交易日为起点。计算只在 Python 发布链路执行，前端不叠加股息率；方法、缺数状态与测试要求见 [TOTAL_RETURN_REQUIREMENTS.md](TOTAL_RETURN_REQUIREMENTS.md)。历史不足、原始价格缺失、分红未核验或检测到未解释公司行为时显示“数据不足”。

首次上线时，须先部署 `20261011100059_stock_total_return_facts.sql`，然后依次运行：

```bash
python -B -m codex.trend_observer.cli sync-fundamentals --force
python -B -m codex.trend_observer.cli sync-market --market CN --force
python -B -m codex.trend_observer.cli publish-dashboard
```

`sync-market --market CN --force` 对股票抓取完整历史以回填未复权价格；正常定时仍为增量抓取。未复权价格优先取东方财富，失败时从腾讯独立原始日线降级；腾讯前复权历史分段抓取并验证重叠价格。旧版行情库中 8 只股票截至 2026-10-09 只有不足三年的历史，迁移本身不会凭空补齐数据。新增股票上线后需检查 `market_daily.raw_close`、`dividend_events` 送转股比例和最新发布版本的两项三年回报；本地代码修改不等于已执行生产回填。

新增陕西煤业（601225）沿用现有股票入库流程，无需数据库迁移。将代码部署到 `main` 后，依次运行 `bootstrap`、`sync-fundamentals --force`、`sync-market --market CN --force`、`publish-dashboard`。**务必等同步任务结束后再发布**，否则新股票会先显示数据延迟或三年回报数据不足。核对最新完整版本的 `payload.assets` 含 26 项，其中股票 14 项；单独检查陕西煤业的历史行情起止日期、财务事实、分红事件、股息率和三年回报状态。中国平安暂不套用工业企业现金流阈值，四维基本面评估保持“数据不足”，直至保险专属规则接入。

### 上线检查清单

一次包含数据库或 Edge 变更的上线，按下面顺序完成：

1. 将包含新计算版本的代码合入 `main`；派生信号版本不一致时，发布器会把对应资产标为“信号版本待重算”，不会继续发布旧结论；
2. 在仓库根目录执行 `supabase db push`，使事实表、数据修复、枚举迁移和 `compute_portfolio_allocation()` 配置计算函数进入正式项目；
3. 部署发生变更的函数（通常为 `supabase functions deploy dashboard-api` 与 `supabase functions deploy portfolio-config`），并等待 Pages 部署静态资源；
4. 对修改过行情、估值或计算规则的版本，依次强制运行受影响市场的 `sync-valuation`、`sync-market`，确认所有资产的 `calculation_version` 已更新后才运行 `publish-dashboard`；
5. 首次或修复基本面后，手动运行 `sync-fundamentals`（需要补抓时选择 `--force`），随后运行 `publish-dashboard`；
6. 验证 `fundamental_assessments`、`financial_facts` 与最新 `dashboard_versions.payload` 均覆盖启用的 14 只股票，并在浏览器确认 `/overview` 的 `allocation` 与 `/asset/{symbol}` 的基本面字段正常返回。中国平安的保险行业专属评估口径尚未接入，原始事实可展示，四维摘要应明确为“数据不足”。

配置偏离、理论调整额及摘要由数据库 RPC 实时计算；若迁移尚未部署，Edge API 不应以浏览器计算或旧版本快照代替该结果。

## 本地查看看板

请从仓库根目录启动仅监听本机的预览服务。它会复制与 GitHub Pages 部署相同的 4 个静态文件，并以 `index.html` 为入口；修改文件后需重启服务：

```bash
python3 scripts/serve_dashboard.py
```

然后访问：

```text
http://127.0.0.1:8766/
```

脚本不会生成数据，也不会写入数据库。看板登录后通过 Edge API 加载已发布版本；缺少认证或 API 不可用时，页面会明确标记并回退内嵌 Mock，Mock 不代表生产跟踪清单。Edge API 仅接受 `DASHBOARD_ALLOWED_ORIGINS` 白名单中的来源；若未允许 `http://127.0.0.1:8766`，本地页只能验收静态页面与 Mock，不能据此断定生产数据链路正常。不要为验收而绕过白名单；真实 API 验收应在获准来源或隔离测试项目进行。预览页面沿用仓库中的 Supabase 配置，登录和“编辑配置”可能访问**生产环境**；测试写入功能前须确认使用了隔离的测试项目。不要在共享网络上提供该页面。

浏览器验收时，先核对页面及 3 个 JS 文件均成功加载、控制台没有页面自身的异常，再验证登录、资产列表、详情弹窗、图表、筛选和缺数据状态；生产结果还须在 GitHub Pages 页面复核。若自动化浏览器本身拒绝本地地址，应遵守该限制，改用获准的浏览器环境或手工验收，不能以换 URL 绕过拒绝。

本地页面和 GitHub Pages 使用同一套登录界面。项目 Ref、API 地址和公开的
`Publishable key`（`sb_publishable_...`）已经写入 `codex/dashboard_config.js`，
登录页只需填写已创建用户的邮箱、密码。用户 access token 和 refresh token 仅保存在
当前标签页的 `sessionStorage`。不要把 secret key 或 `service_role` key 填入页面或
提交到仓库。

看板只通过受控 Edge API 读取数据；资产配置编辑会通过 editor 专用 API 原子写入 Supabase，并在重新加载时读取最新版本。详情弹窗打开时才按需请求历史行情、结构化财务指标、分红事件、行业指标及来源元数据。API 不可用或认证缺失时，页面会明确标记并回退到 Mock。

## 运行测试

```bash
python -m unittest discover -s tests
```

### 本地浏览器验收（固定 Mock）

本机已安装 Google Chrome 时，从仓库根目录执行：

```bash
npm ci
npm run test:browser
```

测试默认使用本机 Chrome，无需另下浏览器。如果本机没有 Chrome，可改用 Playwright 自带的 Chromium：先运行 `npm run browser:setup`，再运行 `PLAYWRIGHT_USE_BUNDLED_CHROMIUM=1 npm run test:browser`。

`test:browser` 会自动在 `127.0.0.1:8767` 启动 `scripts/serve_dashboard.py`，运行完成后关闭服务。测试使用独立浏览器会话，并在加载页面时注入仅指向本机的测试配置；登录、已发布版本、资产详情和配置保存均由固定响应模拟。测试拦截并拒绝所有非本机请求，**不会读取或写入生产 Supabase**。这套结果只证明本地页面交互和错误处理，不能证明生产登录、CORS、数据库或部署正常。

测试覆盖页面与 3 个 JS 文件加载、模拟登录与退出、资产列表和筛选、指数与股票详情、图表悬停标签、缺数及 API 错误、弹窗滚动、模拟配置保存失败和刷新回读；还检查缺失的风格分数不会显示成 0、行业模板不会被某只股票的评估污染，以及 API 文本不会当作 HTML 执行。浏览器测试报告在 `playwright-report/`；失败截图与 trace 在 `test-results/`。这两个目录和 `node_modules/` 已加入 Git 忽略。trace 只含固定测试令牌，但仍建议仅在本机查看。

如果出现 `Executable doesn't exist`，重新运行 `npm run browser:setup`。如果显示 `EADDRINUSE`，先停止占用 8767 端口的进程。如果提示无法绑定回环端口，说明当前执行环境禁止启动本地服务；请在允许本机服务的终端运行上述命令，不要改用其他 URL 绕过浏览器限制。测试失败时先看终端中的用例名，再用 `npx playwright show-report` 打开报告。需要手工预览真实页面时仍使用上文的 8766 端口，并留意该页面沿用生产配置。

## GitHub Actions

`.github/workflows/trend_observer_v2.yml` 按数据类型的预定时间执行增量同步，并在周一至周六早间发布看板版本和推送飞书。

`.github/workflows/deploy_dashboard.yml` 仅打包并发布静态页面与脚本；行情和分红事实由 V2 数据管道维护。

## 架构说明

详见 `ARCHITECTURE.md`。
