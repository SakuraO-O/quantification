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
  const { admin } = authorized;
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
    const { data: allocationRows, error: allocationError } = await admin
      .from("portfolio_allocations")
      .select("allocation_type,category,data_date,value,version")
      .order("version", { ascending: false })
      .order("data_date", { ascending: false });
    if (allocationError) return json(request, { error: "allocation_query_failed" }, 500);
    const latest = new Map<string, Record<string, unknown>>();
    for (const row of allocationRows ?? []) {
      const key = `${row.allocation_type}:${row.category}`;
      if (!latest.has(key)) latest.set(key, row);
    }
    const categories = ["海外", "红利", "成长", "债券", "大宗商品", "现金"];
    let allocation = payload.allocation ?? null;
    if (latest.size === categories.length * 2) {
      const actualTotal = categories.reduce((sum, category) => sum + Number(latest.get(`actual_amount:${category}`)?.value ?? 0), 0);
      const rows = categories.map((category) => {
        const target = Number(latest.get(`target_ratio:${category}`)?.value ?? 0);
        const amount = Number(latest.get(`actual_amount:${category}`)?.value ?? 0);
        const actual = actualTotal > 0 ? amount / actualTotal * 100 : 0;
        const deviation = actual - target;
        return {
          category,
          target_ratio: target,
          actual_amount: amount,
          actual_ratio: actual,
          deviation,
          deviation_state: deviation >= 5 ? "明显超配" : deviation <= -5 ? "明显低配" : "接近目标",
          theoretical_adjustment_amount: actualTotal * target / 100 - amount,
        };
      });
      allocation = {
        rows,
        summary: { total_amount: actualTotal },
        versions: {
          target_ratio: latest.get(`target_ratio:${categories[0]}`)?.version,
          actual_amount: latest.get(`actual_amount:${categories[0]}`)?.version,
        },
      };
    }
    return json(request, { version: version.dashboard_version_id, generated_at: version.generated_at, latest_market_date: version.latest_market_date, completeness: version.completeness, ...payload, allocation });
  }
  if (path === "/indexes") return json(request, { version: version.dashboard_version_id, assets: assets.filter((asset) => asset.asset_type === "指数") });
  if (path === "/stocks") return json(request, { version: version.dashboard_version_id, assets: assets.filter((asset) => asset.asset_type === "股票") });
  if (path === "/style-compass") return json(request, { version: version.dashboard_version_id, results: payload.style_compass ?? [] });
  if (path === "/data-status") return json(request, { version: version.dashboard_version_id, generated_at: version.generated_at, completeness: version.completeness });
  if (path.startsWith("/asset/")) {
    const symbol = decodeURIComponent(path.slice("/asset/".length));
    const asset = assets.find((item) => item.symbol === symbol);
    if (!asset) return json(request, { error: "asset_not_found" }, 404);
    const { data: security } = await admin.from("securities").select("security_id").eq("symbol", symbol).eq("is_active", true).maybeSingle();
    if (!security) return json(request, { error: "asset_not_found" }, 404);
    const range = url.searchParams.get("range") ?? "1y";
    const months = ({ "3m": 3, "6m": 6, "1y": 12, "3y": 36, "5y": 60 } as Record<string, number>)[range] ?? 12;
    const start = new Date(); start.setMonth(start.getMonth() - months);
    const [historyResult, assessmentResult, factsResult, dividendsResult, industryResult, sourcesResult] = await Promise.all([
      admin.from("asset_daily_signals")
        .select("trade_date,close,ma20,ma60,ma120,ma200,short_trend,mid_trend,long_trend,pe,pe_percentile,valuation_status")
        .eq("security_id", security.security_id).gte("trade_date", start.toISOString().slice(0, 10)).order("trade_date"),
      admin.from("fundamental_assessments")
        .select("report_period,dividend_safety_status,operating_quality_status,cash_reinvestment_status,capital_structure_status,fundamental_status,evidence,main_risk,calculation_version,created_at")
        .eq("security_id", security.security_id).order("report_period", { ascending: false }).order("created_at", { ascending: false }).limit(1).maybeSingle(),
      admin.from("financial_facts")
        .select("report_period,metric_code,value,unit,period_type,announcement_date,version")
        .eq("security_id", security.security_id).eq("is_current", true).order("report_period", { ascending: false }).limit(160),
      admin.from("dividend_events")
        .select("fiscal_year,event_stage,announcement_id,cash_dividend_per_share,cash_dividend_total,ex_date,payment_date,announcement_date")
        .eq("security_id", security.security_id).order("fiscal_year", { ascending: false }).limit(20),
      admin.from("industry_metric_values")
        .select("period,metric_code,value,unit,confirmation_status,version")
        .eq("security_id", security.security_id).eq("confirmation_status", "confirmed").order("period", { ascending: false }).limit(80),
      admin.from("source_documents")
        .select("source,source_record_id,title,document_type,report_period,announcement_date,document_url,content_hash,fetched_at")
        .eq("security_id", security.security_id).order("announcement_date", { ascending: false }).limit(40),
    ]);
    if ([historyResult, assessmentResult, factsResult, dividendsResult, industryResult, sourcesResult].some((result) => result.error)) {
      return json(request, { error: "asset_detail_query_failed" }, 500);
    }
    return json(request, {
      version: version.dashboard_version_id,
      asset,
      range,
      history: historyResult.data ?? [],
      fundamental_assessment: assessmentResult.data,
      financial_facts: factsResult.data ?? [],
      dividend_events: dividendsResult.data ?? [],
      industry_metrics: industryResult.data ?? [],
      sources: sourcesResult.data ?? [],
    });
  }
  return json(request, { error: "not_found" }, 404);
});
