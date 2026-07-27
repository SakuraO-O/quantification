// Read-only API for the Trend Observer dashboard.
// Deploy with: supabase functions deploy dashboard-api --no-verify-jwt
// The function verifies Auth itself so browser clients never receive a service key.

import { authorizedClients, corsHeaders } from "../_shared/supabase.ts";

function json(request: Request, body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...corsHeaders(request), "Content-Type": "application/json" } });
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders(request) });
  if (request.method !== "GET") return json(request, { error: "method_not_allowed" }, 405);

  const url = new URL(request.url);
  const authHeader = request.headers.get("Authorization");
  if (!authHeader) return json(request, { error: "unauthorized" }, 401);
  const authorized = await authorizedClients(authHeader, ["viewer", "editor"]);
  if ("error" in authorized) return json(request, { error: authorized.error }, authorized.status);
  const { admin, user, role } = authorized;
  // A named permissions array is the authoritative granular entitlement.  The
  // role fallback keeps the existing single-user deployment working until an
  // administrator explicitly adds app_metadata.permissions to a user.
  const configuredPermissions = user.app_metadata?.permissions;
  const permissions = new Set(
    Array.isArray(configuredPermissions)
      ? configuredPermissions.map(String)
      : ["view_dashboard", "view_fundamentals", ...(role === "editor" ? ["edit_configuration"] : [])],
  );
  const canViewFundamentals = permissions.has("view_fundamentals");
  const { data: version, error } = await admin
    .from("dashboard_versions")
    .select("dashboard_version_id,generated_at,latest_market_date,completeness,payload")
    .eq("is_complete", true)
    .order("generated_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) return json(request, { error: "dashboard_query_failed" }, 500);
  if (!version) return json(request, { error: "dashboard_not_ready" }, 503);

  const path = url.pathname.replace(/^.*dashboard-api/, "") || "/overview";
  const payload = version.payload as Record<string, unknown>;
  const assets = Array.isArray(payload.assets) ? payload.assets as Array<Record<string, unknown>> : [];
  if (path === "/overview") {
    // The RPC is the sole source for allocation ratios, thresholds, and text.
    // Do not derive these values from a dashboard-version snapshot in Edge.
    const { data: allocation, error: allocationError } = await admin.rpc("compute_portfolio_allocation");
    if (allocationError) return json(request, { error: "allocation_query_failed" }, 500);
    return json(request, { version: version.dashboard_version_id, generated_at: version.generated_at, latest_market_date: version.latest_market_date, completeness: version.completeness, permissions: [...permissions], ...payload, allocation });
  }
  if (path === "/indexes") return json(request, { version: version.dashboard_version_id, assets: assets.filter((asset) => asset.asset_type === "指数") });
  if (path === "/stocks") return json(request, { version: version.dashboard_version_id, assets: assets.filter((asset) => asset.asset_type === "股票") });
  if (path === "/style-compass") return json(request, { version: version.dashboard_version_id, results: payload.style_compass ?? [] });
  if (path === "/data-status") return json(request, { version: version.dashboard_version_id, generated_at: version.generated_at, completeness: version.completeness });
  if (path.startsWith("/asset/")) {
    const symbol = decodeURIComponent(path.slice("/asset/".length));
    const asset = assets.find((item) => item.symbol === symbol);
    if (!asset) return json(request, { error: "asset_not_found" }, 404);
    const { data: security } = await admin.from("securities").select("security_id")
      .eq("symbol", symbol).eq("market", String(asset.market ?? "")).eq("is_active", true).maybeSingle();
    if (!security) return json(request, { error: "asset_not_found" }, 404);
    const range = url.searchParams.get("range") ?? "1y";
    const months = ({ "3m": 3, "6m": 6, "1y": 12, "3y": 36, "5y": 60 } as Record<string, number>)[range] ?? 12;
    const start = new Date(); start.setMonth(start.getMonth() - months);
    const historyResult = await admin.from("asset_daily_signals")
      .select("trade_date,close,ma20,ma60,ma120,ma200,short_trend,mid_trend,long_trend,pe,pe_percentile,valuation_status")
      .eq("security_id", security.security_id).gte("trade_date", start.toISOString().slice(0, 10)).order("trade_date");
    if (historyResult.error) return json(request, { error: "asset_detail_query_failed" }, 500);
    if (!canViewFundamentals) {
      return json(request, { version: version.dashboard_version_id, asset, range, history: historyResult.data ?? [], fundamentals_access: false });
    }
    const [assessmentResult, factsResult, dividendsResult, industryResult, sourcesResult] = await Promise.all([
      admin.from("fundamental_assessments").select("report_period,dividend_safety_status,operating_quality_status,cash_reinvestment_status,capital_structure_status,fundamental_status,evidence,main_risk,calculation_version,created_at").eq("security_id", security.security_id).order("report_period", { ascending: false }).order("created_at", { ascending: false }).limit(1).maybeSingle(),
      admin.from("financial_facts").select("report_period,metric_code,value,unit,period_type,announcement_date,version").eq("security_id", security.security_id).eq("is_current", true).order("report_period", { ascending: false }).limit(160),
      admin.from("dividend_events").select("fiscal_year,event_stage,announcement_id,cash_dividend_per_share,cash_dividend_total,ex_date,payment_date,announcement_date").eq("security_id", security.security_id).order("fiscal_year", { ascending: false }).limit(20),
      admin.from("industry_metric_values").select("period,metric_code,value,unit,confirmation_status,version").eq("security_id", security.security_id).eq("confirmation_status", "confirmed").order("period", { ascending: false }).limit(80),
      admin.from("source_documents").select("source,source_record_id,title,document_type,report_period,announcement_date,document_url,content_hash,fetched_at").eq("security_id", security.security_id).order("announcement_date", { ascending: false }).limit(40),
    ]);
    if ([assessmentResult, factsResult, dividendsResult, industryResult, sourcesResult].some((result) => result.error)) {
      return json(request, { error: "asset_detail_query_failed" }, 500);
    }
    return json(request, {
      version: version.dashboard_version_id,
      asset,
      range,
      history: historyResult.data ?? [],
      fundamentals_access: true,
      fundamental_assessment: assessmentResult.data,
      financial_facts: factsResult.data ?? [],
      dividend_events: dividendsResult.data ?? [],
      industry_metrics: industryResult.data ?? [],
      sources: sourcesResult.data ?? [],
    });
  }
  return json(request, { error: "not_found" }, 404);
});
