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

    def test_dashboard_publisher_uses_store_select_contract(self):
        publisher = (self.root / "codex/trend_observer/dashboard_versions.py").read_text(encoding="utf-8")
        self.assertNotIn("\n            columns=", publisher)
        self.assertIn('select="fiscal_year,cash_dividend_per_share', publisher)


if __name__ == "__main__":
    unittest.main()
