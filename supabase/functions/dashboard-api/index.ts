// Read-only API for the Trend Observer dashboard.
// Deploy with: supabase functions deploy dashboard-api --no-verify-jwt
// The function verifies Auth itself so browser clients never receive a service key.

import { authorizedClients, corsHeaders } from "../_shared/supabase.ts";
import {safeSummary, applyReviewInvalidations} from "../_shared/review.ts";

function json(request: Request, body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...corsHeaders(request), "Content-Type": "application/json" } });
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders(request) });
  if (request.method !== "GET") return json(request, { error: "method_not_allowed" }, 405);

  const url = new URL(request.url);
  const authHeader = request.headers.get("Authorization");
  if (!authHeader) return json(request, { error: "unauthorized" }, 401);
  const authorized = await authorizedClients(authHeader);
  if ("error" in authorized) return json(request, { error: authorized.error }, authorized.status);
  const { admin } = authorized;
  const permissions = new Set(["view_dashboard", "view_fundamentals", "edit_configuration"]);
  const canViewFundamentals = true;
  const {data: runtime, error: runtimeError} = await admin.rpc("dashboard_runtime_status");
  if (runtimeError) return json(request, {error:"dashboard_runtime_query_failed"}, 500);
  let versionQuery = admin
    .from("dashboard_versions")
    .select("dashboard_version_id,generated_at,latest_market_date,completeness,payload")
    .eq("is_complete", true)
    .order("generated_at", { ascending: false }).limit(1);
  const requestedVersion = url.searchParams.get("version");
  if (requestedVersion || runtime?.current_version_id) versionQuery = versionQuery.eq("dashboard_version_id", requestedVersion || runtime.current_version_id);
  const { data: version, error } = await versionQuery.maybeSingle();
  if (error) return json(request, { error: "dashboard_query_failed" }, 500);
  if (!version) return json(request, { error: "dashboard_not_ready" }, 503);

  const path = url.pathname.replace(/^.*dashboard-api/, "") || "/overview";
  const payload = version.payload as Record<string, unknown>;
  const reviewResult = await admin.rpc("review_overview", {p_version:version.dashboard_version_id});
  if (reviewResult.error) return json(request,{error:"review_status_unavailable"},503);
  const review = safeSummary(reviewResult.data);
  const assets = applyReviewInvalidations(Array.isArray(payload.assets) ? payload.assets as Array<Record<string, unknown>> : [], review);
  const marketDates = ["CN", "HK", "US"].filter(market => assets.some(asset => asset.market === market)).map(market => ({
    market,
    dates: [...new Set(assets.filter(asset => asset.market === market).map(asset => asset.last_valid_trade_date || asset.trade_date).filter(Boolean))].sort(),
    delayed: assets.some(asset => asset.market === market && asset.data_status === "delayed"),
  }));
  const withoutQuarterly = (item: Record<string, unknown>) => {
    const { quarterly: _quarterly, ...summary } = item;
    return summary;
  };
  if (path === "/overview") {
    // The RPC is the sole source for allocation ratios, thresholds, and text.
    // Do not derive these values from a dashboard-version snapshot in Edge.
    const { data: allocation, error: allocationError } = await admin.rpc("compute_portfolio_allocation");
    if (allocationError) return json(request, { error: "allocation_query_failed" }, 500);
    return json(request, { version: version.dashboard_version_id, generated_at: version.generated_at, latest_market_date: version.latest_market_date, completeness: version.completeness, permissions: [...permissions], ...payload, review, runtime, market_dates: marketDates, assets: assets.map(withoutQuarterly), allocation });
  }
  if (path === "/indexes") return json(request, { version: version.dashboard_version_id, assets: assets.filter((asset) => asset.asset_type === "指数").map(withoutQuarterly) });
  if (path === "/stocks") return json(request, { version: version.dashboard_version_id, assets: assets.filter((asset) => asset.asset_type === "股票").map(withoutQuarterly) });
  if (path === "/style-compass") return json(request, { version: version.dashboard_version_id, results: payload.style_compass ?? [] });
  if (path === "/data-status") return json(request, { version: version.dashboard_version_id, generated_at: version.generated_at, runtime, review, market_dates: marketDates, completeness: version.completeness });
  if (path.startsWith("/asset/")) {
    const symbol = decodeURIComponent(path.slice("/asset/".length));
    const asset = assets.find((item) => item.symbol === symbol);
    if (!asset) return json(request, { error: "asset_not_found" }, 404);
    const { data: security } = await admin.from("securities").select("security_id")
      .eq("symbol", symbol).eq("market", String(asset.market ?? "")).eq("is_active", true).maybeSingle();
    if (!security) return json(request, { error: "asset_not_found" }, 404);
    const section = url.searchParams.get("section") ?? "all";
    if (!["all", "history", "fundamentals"].includes(section)) return json(request, {error:"invalid_section"}, 400);
    if (section === "fundamentals" && !canViewFundamentals) return json(request, {error:"forbidden"}, 403);
    const range = url.searchParams.get("range") ?? "1y";
    const months = ({ "3m": 3, "6m": 6, "1y": 12, "3y": 36, "5y": 60 } as Record<string, number>)[range] ?? 12;
    const historyEnd = String(asset.last_valid_trade_date || asset.trade_date || "").slice(0, 10);
    if (section !== "fundamentals" && !/^\d{4}-\d{2}-\d{2}$/.test(historyEnd)) return json(request, {error:"asset_market_date_missing"}, 503);
    const start = new Date(`${historyEnd}T00:00:00Z`); start.setUTCMonth(start.getUTCMonth() - months);
    const history: Array<Record<string, unknown>> = [];
    if (section !== "fundamentals") {
      for (let offset = 0; ; offset += 1000) {
        const page = await admin.from("asset_daily_signals")
          .select("trade_date,close,ma20,ma60,ma120,ma200,short_trend,mid_trend,long_trend,pe,pe_percentile,valuation_status")
          .eq("security_id", security.security_id).gte("trade_date", start.toISOString().slice(0, 10))
          .lte("trade_date", historyEnd).order("trade_date").range(offset, offset + 999);
        if (page.error) return json(request, { error: "asset_detail_query_failed" }, 500);
        history.push(...(page.data ?? []));
        if ((page.data ?? []).length < 1000) break;
      }
    }
    const peHistory: Array<{ trade_date: string; pe: number | null; pe_percentile: number | null; valuation_status: string | null }> = [];
    if (asset.asset_type === "指数" && section !== "fundamentals") {
      const peEnd = historyEnd;
      const peStart = new Date(peEnd);
      peStart.setFullYear(peStart.getFullYear() - 10);
      for (let offset = 0; ; offset += 1000) {
        const page = await admin.from("asset_daily_signals")
          .select("trade_date,pe,pe_percentile,valuation_status")
          .eq("security_id", security.security_id).gte("trade_date", peStart.toISOString().slice(0, 10)).lte("trade_date", peEnd)
          .not("pe", "is", null).order("trade_date").range(offset, offset + 999);
        if (page.error) return json(request, { error: "asset_pe_history_query_failed" }, 500);
        peHistory.push(...(page.data ?? []));
        if ((page.data ?? []).length < 1000) break;
      }
    }
    if (!canViewFundamentals || section === "history") {
      return json(request, { version: version.dashboard_version_id, asset: withoutQuarterly(asset), range, history, pe_history: peHistory, fundamentals_access: false });
    }
    const [assessmentResult, factsResult, dividendsResult, industryResult, sourcesResult] = await Promise.all([
      admin.from("fundamental_assessments").select("report_period,dividend_safety_status,operating_quality_status,cash_reinvestment_status,capital_structure_status,fundamental_status,evidence,main_risk,calculation_version,created_at").eq("security_id", security.security_id).order("report_period", { ascending: false }).order("created_at", { ascending: false }).limit(1).maybeSingle(),
      admin.from("financial_facts").select("report_period,metric_code,value,unit,period_type,announcement_date,version,verification_status").eq("security_id", security.security_id).eq("is_current", true).order("report_period", { ascending: false }).limit(160),
      admin.from("dividend_events").select("fiscal_year,report_period,event_stage,announcement_id,cash_dividend_per_share,cash_dividend_total,ex_date,payment_date,announcement_date,verification_status").eq("security_id", security.security_id).eq("is_current", true).not("report_period", "is", null).order("fiscal_year", { ascending: false }).limit(20),
      admin.from("industry_metric_values").select("period,metric_code,value,unit,confirmation_status,version").eq("security_id", security.security_id).eq("confirmation_status", "confirmed").order("period", { ascending: false }).limit(80),
      admin.from("source_documents").select("source,source_record_id,title,document_type,report_period,announcement_date,document_url,content_hash,fetched_at").eq("security_id", security.security_id).order("announcement_date", { ascending: false }).limit(40),
    ]);
    if ([assessmentResult, factsResult, dividendsResult, industryResult, sourcesResult].some((result) => result.error)) {
      return json(request, { error: "asset_detail_query_failed" }, 500);
    }
    return json(request, {
      version: version.dashboard_version_id,
      asset: withoutQuarterly(asset),
      quarterly: asset.asset_type === "股票" ? asset.quarterly ?? null : null,
      range,
      history,
      pe_history: peHistory,
      fundamentals_access: true,
      fundamental_assessment: Object.hasOwn(asset,"fundamental_assessment") ? asset.fundamental_assessment : assessmentResult.data,
      financial_facts: factsResult.data ?? [],
      dividend_events: dividendsResult.data ?? [],
      industry_metrics: industryResult.data ?? [],
      sources: sourcesResult.data ?? [],
    });
  }
  return json(request, { error: "not_found" }, 404);
});
