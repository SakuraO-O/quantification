// Authenticated write API for the six manually maintained allocation categories.
// Deploy with: supabase functions deploy portfolio-config --no-verify-jwt

import { authorizedClients, corsHeaders } from "../_shared/supabase.ts";

const categories = ["海外", "红利", "成长", "债券", "大宗商品", "现金"];

function json(request: Request, body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...corsHeaders(request), "Content-Type": "application/json" } });
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders(request) });
  if (request.method !== "POST") return json(request, { error: "method_not_allowed" }, 405);
  const authHeader = request.headers.get("Authorization");
  if (!authHeader) return json(request, { error: "unauthorized" }, 401);
  const authorized = await authorizedClients(authHeader, ["editor"]);
  if ("error" in authorized) return json(request, { error: authorized.error }, authorized.status);
  const { admin, user } = authorized;

  const body = await request.json();
  const allocationType = body.allocation_type;
  const dataDate = body.data_date;
  const values = body.values;
  if (!['target_ratio', 'actual_amount'].includes(allocationType) || !/^\d{4}-\d{2}-\d{2}$/.test(dataDate) || !Array.isArray(values)) {
    return json(request, { error: "invalid_payload" }, 400);
  }
  const valuesByCategory = new Map(values.map((item: { category: string; value: number }) => [item.category, Number(item.value)]));
  if (values.length !== categories.length || categories.some((category) => !valuesByCategory.has(category)) || [...valuesByCategory.values()].some((value) => !Number.isFinite(value) || value < 0)) {
    return json(request, { error: "invalid_categories_or_values" }, 400);
  }
  const total = [...valuesByCategory.values()].reduce((sum, value) => sum + value, 0);
  if (allocationType === 'target_ratio' && Math.abs(total - 100) > 0.0001) return json(request, { error: "target_ratio_must_total_100" }, 400);
  if (allocationType === 'actual_amount' && total <= 0) return json(request, { error: "actual_amount_must_not_be_all_zero" }, 400);

  const rpcValues = categories.map((category) => ({ category, value: valuesByCategory.get(category) }));
  const { data: version, error: insertError } = await admin.rpc("save_portfolio_allocation", {
    p_allocation_type: allocationType,
    p_data_date: dataDate,
    p_values: rpcValues,
    p_created_by: user.id,
  });
  if (insertError) return json(request, { error: "allocation_save_failed" }, 500);
  return json(request, { allocation_type: allocationType, data_date: dataDate, version });
});
