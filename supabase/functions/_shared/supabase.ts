import { createClient } from "npm:@supabase/supabase-js@2.106.2";

function requiredEnv(name: string): string {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`missing_environment_variable:${name}`);
  return value;
}

function namedKey(jsonName: string, singleName: string, legacyName: string): string {
  const named = Deno.env.get(jsonName)?.trim();
  if (named) {
    const parsed = JSON.parse(named) as Record<string, string>;
    const key = parsed.default || Object.values(parsed)[0];
    if (key) return key;
  }
  const single = Deno.env.get(singleName)?.trim();
  if (single) return single;
  const legacy = Deno.env.get(legacyName)?.trim();
  if (legacy) return legacy;
  throw new Error(`missing_supabase_key:${jsonName}`);
}

export function supabaseClients(authHeader: string) {
  const url = requiredEnv("SUPABASE_URL");
  const publishableKey = namedKey("SUPABASE_PUBLISHABLE_KEYS", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY");
  const secretKey = namedKey("SUPABASE_SECRET_KEYS", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY");
  return {
    viewer: createClient(url, publishableKey, {
      auth: { autoRefreshToken: false, persistSession: false },
      global: { headers: { Authorization: authHeader } },
    }),
    admin: createClient(url, secretKey, {
      auth: { autoRefreshToken: false, persistSession: false },
    }),
  };
}

export async function authorizedClients(authHeader: string, allowedRoles: string[]) {
  const clients = supabaseClients(authHeader);
  const { data, error } = await clients.viewer.auth.getUser();
  if (error || !data.user) return { error: "unauthorized" as const, status: 401, ...clients };
  const role = String(data.user.app_metadata?.role || "");
  if (!allowedRoles.includes(role)) return { error: "forbidden" as const, status: 403, ...clients };
  return { user: data.user, role, ...clients };
}

function normalizedOrigin(value: string): string | null {
  try {
    return new URL(value.trim()).origin;
  } catch {
    return null;
  }
}

export function corsHeaders(request: Request) {
  const configured = Deno.env.get("DASHBOARD_ALLOWED_ORIGINS")
    || Deno.env.get("DASHBOARD_ALLOWED_ORIGIN")
    || "";
  const allowed = configured.split(",").map(normalizedOrigin).filter(Boolean);
  const requestOrigin = request.headers.get("Origin");
  const origin = requestOrigin ? normalizedOrigin(requestOrigin) : null;
  return {
    ...(origin && allowed.includes(origin) ? { "Access-Control-Allow-Origin": origin } : {}),
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Vary": "Origin",
  };
}
