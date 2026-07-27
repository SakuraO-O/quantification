import unittest
from pathlib import Path


class DashboardV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.html = (cls.root / "codex/dashboard_example.html").read_text(encoding="utf-8")
        cls.runtime = (cls.root / "codex/dashboard_runtime.js").read_text(encoding="utf-8")
        cls.auth = (cls.root / "codex/dashboard_auth.js").read_text(encoding="utf-8")
        cls.config = (cls.root / "codex/dashboard_config.js").read_text(encoding="utf-8")
        cls.api = (cls.root / "supabase/functions/dashboard-api/index.ts").read_text(encoding="utf-8")
        cls.shared = (cls.root / "supabase/functions/_shared/supabase.ts").read_text(encoding="utf-8")
        cls.portfolio_api = (cls.root / "supabase/functions/portfolio-config/index.ts").read_text(encoding="utf-8")
        cls.supabase_config = (cls.root / "supabase/config.toml").read_text(encoding="utf-8")

    def test_new_dashboard_uses_runtime_not_legacy_json(self):
        self.assertIn("dashboard_runtime.js", self.html)
        self.assertIn("dashboard_auth.js", self.html)
        self.assertNotIn("fetch('./dashboard_data.json", self.html)
        self.assertNotIn("投资决策看板原型</title>", self.html)

    def test_mock_scope_and_api_contract(self):
        self.assertEqual(self.runtime.count('name:"'), 5)
        self.assertIn("apiFetch(\"/overview\")", self.runtime)
        self.assertIn("encodeURIComponent(asset.code)", self.runtime)
        self.assertIn("apiIndices.length !== 12 || apiStocks.length !== 9", self.runtime)

    def test_auth_uses_publishable_key_and_session_only_tokens(self):
        self.assertIn("calebaqhmihatnssjgon", self.config)
        self.assertIn("sb_publishable_", self.auth)
        self.assertIn("/auth/v1/token?grant_type=", self.auth)
        self.assertIn("sessionStorage.setItem(storage.access", self.auth)
        self.assertNotIn("localStorage.setItem(storage.access", self.auth)
        self.assertIn("apikey: key", self.runtime)
        self.assertNotIn("auth-publishable", self.html)
        self.assertNotIn("auth-key-row", self.html)

    def test_asset_detail_is_structured_and_bounded(self):
        self.assertIn('from("fundamental_assessments")', self.api)
        self.assertIn('from("financial_facts")', self.api)
        self.assertIn('from("source_documents")', self.api)
        self.assertIn(".limit(160)", self.api)
        self.assertNotIn("storage.", self.api)
        self.assertIn('.eq("market", String(asset.market ?? ""))', self.api)

    def test_edge_functions_support_new_api_keys(self):
        self.assertIn("SUPABASE_PUBLISHABLE_KEYS", self.shared)
        self.assertIn("SUPABASE_SECRET_KEYS", self.shared)
        self.assertIn("@supabase/supabase-js@2.106.2", self.shared)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", self.api)

    def test_single_user_editor_authorization_and_persistence(self):
        self.assertIn("enable_signup = false", self.supabase_config)
        self.assertIn("user.app_metadata?.role", self.shared)
        self.assertNotIn("user_metadata", self.shared)
        self.assertIn('["viewer", "editor"]', self.api)
        self.assertIn('["editor"]', self.portfolio_api)
        self.assertIn('.rpc("save_portfolio_allocation"', self.portfolio_api)
        self.assertIn("saveAllocation", self.runtime)
        self.assertIn("__trendDashboardRuntime.saveAllocation", self.html)

    def test_dashboard_supports_granular_fundamentals_permission(self):
        self.assertIn('user.app_metadata?.permissions', self.api)
        self.assertIn('"view_fundamentals"', self.api)
        self.assertIn('fundamentals_access: false', self.api)
        self.assertIn('runtimeState.permissions.has("view_fundamentals")', self.runtime)
        self.assertIn('data-pane="annual"', self.runtime)

    def test_pages_bundle_contains_runtime_scripts_and_schedule_reaches_0930(self):
        pages = (self.root / ".github/workflows/deploy_dashboard.yml").read_text(encoding="utf-8")
        legacy = (self.root / ".github/workflows/daily_trend_observer.yml").read_text(encoding="utf-8")
        pipeline = (self.root / ".github/workflows/trend_observer_v2.yml").read_text(encoding="utf-8")
        for script in ("dashboard_config.js", "dashboard_auth.js", "dashboard_runtime.js"):
            self.assertIn(script, pages)
        self.assertIn("branches: [main]", pages)
        self.assertNotIn("deploy-pages", legacy)
        self.assertIn('"0,10,20,30,40,50 0 * * 1-6"', pipeline)
        self.assertIn('"0,10,20,30 1 * * 1-6"', pipeline)
        self.assertIn("sync-valuation --market CN --force", pipeline)
        self.assertNotIn("sync-market --market CN --force", pipeline)
        fundamentals_start = pipeline.index('elif [ "$SCHEDULE" = "30 23 * * *" ]; then')
        dispatch_window_start = pipeline.index("          else\n            python -B -m codex.trend_observer.cli publish-dashboard")
        fundamentals_block = pipeline[fundamentals_start:dispatch_window_start]
        self.assertIn("python -B -m codex.trend_observer.cli sync-fundamentals", fundamentals_block)
        self.assertIn("python -B -m codex.trend_observer.cli publish-dashboard", fundamentals_block)

    def test_dashboard_publisher_uses_store_select_contract(self):
        publisher = (self.root / "codex/trend_observer/dashboard_versions.py").read_text(encoding="utf-8")
        self.assertNotIn("\n            columns=", publisher)
        self.assertIn('select="fiscal_year,cash_dividend_per_share', publisher)

    def test_modal_chart_tooltips_render_in_the_dialog_top_layer(self):
        # Index and stock price views share drawApiPriceChart; stock research
        # charts use bindResearchTooltips. Both must use the dialog-aware host.
        self.assertIn("drawApiPriceChart", self.runtime)
        self.assertIn("bindResearchTooltips", self.runtime)
        self.assertIn('canvas.closest("dialog[open]") || canvas.closest("dialog") || document.body', self.runtime)
        self.assertIn('target.closest("dialog[open]") || target.closest("dialog") || document.body', self.runtime)
        self.assertIn('window.innerWidth - bounds.width - 8', self.runtime)
        self.assertIn('数据：本地 Mock', self.runtime)
        self.assertIn('z-index:1000', self.html)

    def test_detail_history_uses_loading_state_before_declaring_no_data(self):
        self.assertIn("historyRanges: new Map()", self.runtime)
        self.assertIn("detailErrors: new Map()", self.runtime)
        self.assertEqual(self.runtime.count('drawLoadingChart(canvas, "正在加载")'), 2)
        self.assertIn("function drawLoadingChart", self.runtime)

    def test_open_detail_locks_page_scroll_to_modal_content(self):
        self.assertIn("body:has(dialog[open]){overflow:hidden}", self.html)
        self.assertIn(".modal-body{max-height:calc(90vh - 72px)", self.html)
        self.assertIn("overscroll-behavior:contain", self.html)

    def test_api_and_dashboard_use_canonical_display_enums(self):
        self.assertIn('value="高估">高估</label>', self.html)
        self.assertIn('value="极高估">极高估</label>', self.html)
        self.assertNotIn('"偏高"', self.api)
        self.assertIn('rpc("compute_portfolio_allocation")', self.api)
        self.assertNotIn('Math.abs(deviation)', self.api)
        self.assertNotIn('"接近目标"', self.runtime)

    def test_dashboard_renders_server_owned_style_and_allocation_rules(self):
        allocation_migration = (self.root / "supabase/migrations/20260726022358_compute_portfolio_allocation.sql").read_text(encoding="utf-8")
        self.assertIn("compute_portfolio_allocation", allocation_migration)
        self.assertIn("'deviation_state'", allocation_migration)
        self.assertIn("'summary'", allocation_migration)
        self.assertIn("recommendationReason: item.recommendation_reason", self.runtime)
        self.assertIn("const recommendation = pair.recommendation", self.runtime)
        self.assertIn("runtimeState.allocation?.summary?.text", self.runtime)
        self.assertNotIn("winnerPe", self.runtime)
        self.assertNotIn("Math.abs(deviation) <=", self.runtime)

    def test_login_wait_keeps_mock_status_explicit(self):
        self.assertIn('setSourceStatus("mock", "本地 Mock · 登录后读取生产数据")', self.runtime)
        self.assertLess(
            self.runtime.index('setSourceStatus("mock", "本地 Mock · 登录后读取生产数据")'),
            self.runtime.index("await window.TrendDashboardAuth.ensureAuthenticated()"),
        )

    def test_filter_enums_and_mock_focus_follow_dashboard_rules(self):
        self.assertIn('value="暂停参与">暂停参与</label>', self.html)
        self.assertIn('value="趋势分歧">趋势分歧</label>', self.html)
        self.assertIn('value="下跌通道">下跌通道</label>', self.html)
        self.assertIn('value="暂停关注">暂停关注</label>', self.html)
        self.assertNotIn("const data=[{type:'index'", self.html)
        self.assertIn("const candidates = [", self.runtime)
        self.assertIn("const statusMetric = focusStatuses.has(item.asset.status)", self.runtime)

    def test_list_filters_are_multi_select(self):
        self.assertEqual(self.html.count('class="filter-select"'), 6)
        self.assertIn('class="filter-tag"', self.html)
        self.assertIn('function initFilterSelects()', self.html)
        self.assertIn("clear.title='清空筛选'", self.html)
        self.assertIn("[...node.querySelectorAll('input:checked')].forEach(input=>input.checked=false)", self.html)
        self.assertIn("function selectedFilters(selector)", self.html)
        self.assertIn("adv.includes(a.advice)", self.html)

    def test_only_one_local_stock_detail_fallback_remains(self):
        self.assertEqual(self.html.count("function stockDetail(a)"), 1)
        self.assertNotIn("季度基本面趋势</h4><small>最近8个季度 · 原型示例", self.runtime)


if __name__ == "__main__":
    unittest.main()
