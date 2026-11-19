import { assertEquals, assert } from "jsr:@std/assert@1";

// Run the real Edge handler and Supabase client against in-memory HTTP responses.
// The fake fetch rejects every unrecognized URL, including any external request.
let handler: (request: Request) => Promise<Response>;
const originalServe = Deno.serve;
const originalFetch = globalThis.fetch;
const envNames = ["SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEYS", "SUPABASE_SECRET_KEYS"];
const originalEnv = envNames.map(name => Deno.env.get(name));
Deno.env.set("SUPABASE_URL", "https://edge-test.invalid");
Deno.env.set("SUPABASE_PUBLISHABLE_KEYS", JSON.stringify({default:"sb_publishable_test"}));
Deno.env.set("SUPABASE_SECRET_KEYS", JSON.stringify({default:"sb_secret_test"}));
Deno.serve = ((callback: typeof handler) => { handler = callback; return {} as Deno.HttpServer; }) as typeof Deno.serve;
let permissions: string[];
let calls: URL[];
let historyFailure: boolean;
let marketDate: string | null;
let assetType: string;

const rows = Array.from({length:1103}, (_, index) => ({trade_date:`day-${index}`, close:index}));
function reset() {
  permissions = ["view_dashboard", "view_fundamentals"];
  calls = [];
  historyFailure = false;
  marketDate = "2026-11-18";
  assetType = "股票";
}
reset();
globalThis.fetch = ((input: RequestInfo | URL) => {
  const url = new URL(input instanceof Request ? input.url : String(input));
  if (url.origin !== "https://edge-test.invalid") throw new Error("Unexpected external request");
  calls.push(url);
  const json = (data: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(data), {status, headers:{"Content-Type":"application/json"}}));
  if (url.pathname === "/auth/v1/user") return json({id:"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", app_metadata:{role:"viewer", permissions}});
  if (url.pathname === "/rest/v1/rpc/dashboard_runtime_status") return json({current_version_id:"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"});
  if (url.pathname === "/rest/v1/dashboard_versions") return json([{
    dashboard_version_id:"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", payload:{assets:[{
      symbol:"sh600938", market:"CN", asset_type:assetType, trade_date:marketDate,
      quarterly:{status:"已计算", years:[2026]}
    }]}
  }]);
  if (url.pathname === "/rest/v1/securities") return json([{security_id:"cccccccc-cccc-cccc-cccc-cccccccccccc"}]);
  if (url.pathname === "/rest/v1/asset_daily_signals") {
    if (historyFailure) return json({message:"history unavailable"}, 500);
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 1000);
    return json(rows.slice(offset, offset + limit));
  }
  if (["fundamental_assessments","financial_facts","dividend_events","industry_metric_values","source_documents"].some(table => url.pathname === `/rest/v1/${table}`)) return json([]);
  throw new Error(`Unmocked request ${url.pathname}`);
}) as typeof fetch;
await import("./index.ts");
Deno.serve = originalServe;

function request(section: string) {
  return new Request(`https://edge-test.invalid/functions/v1/dashboard-api/asset/sh600938?section=${section}&range=5y`, {headers:{Authorization:"Bearer test"}});
}
Deno.test("five-year history is paginated and bounded by the published market date", async () => {
  reset();
  const response = await handler(request("history"));
  assertEquals(response.status, 200);
  const body = await response.json();
  assertEquals(body.history.length, 1103);
  const pages = calls.filter(url => url.pathname.endsWith("asset_daily_signals"));
  assertEquals(pages.length, 2);
  assert(pages[0].searchParams.getAll("trade_date").includes("lte.2026-11-18"));
  assert(pages[0].searchParams.getAll("trade_date").includes("gte.2021-11-18"));
});
Deno.test("fundamentals succeeds independently of missing or failing market history", async () => {
  reset(); historyFailure = true; marketDate = null;
  const response = await handler(request("fundamentals"));
  assertEquals(response.status, 200);
  assertEquals((await response.json()).quarterly.years, [2026]);
  assertEquals(calls.filter(url => url.pathname.endsWith("asset_daily_signals")).length, 0);
});
Deno.test("view-only access cannot read quarterly fundamentals", async () => {
  reset(); permissions = ["view_dashboard"];
  const response = await handler(request("fundamentals"));
  assertEquals(response.status, 403);
  await response.body?.cancel();
  assertEquals(calls.filter(url => url.pathname.endsWith("financial_facts")).length, 0);
});
Deno.test("role without dashboard permission cannot read any dashboard data", async () => {
  reset(); permissions = [];
  const response = await handler(request("history"));
  assertEquals(response.status, 403);
  await response.body?.cancel();
  assertEquals(calls.filter(url => url.pathname.startsWith("/rest/")).length, 0);
});
Deno.test("history query failure remains local to the history response", async () => {
  reset(); historyFailure = true;
  const response = await handler(request("history"));
  assertEquals(response.status, 500);
  await response.body?.cancel();
});
Deno.test("restore process test boundaries", () => {
  globalThis.fetch = originalFetch;
  envNames.forEach((name, i) => originalEnv[i] === undefined ? Deno.env.delete(name) : Deno.env.set(name, originalEnv[i]!));
});
