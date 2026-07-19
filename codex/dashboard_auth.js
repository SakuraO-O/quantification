(() => {
  "use strict";

  const config = window.TREND_DASHBOARD_CONFIG || {};
  const storage = {
    access: "trend-dashboard-access-token",
    refresh: "trend-dashboard-refresh-token",
    expires: "trend-dashboard-token-expires-at"
  };
  let pendingResolve = null;
  let pendingPromise = null;

  const publishableKey = () => String(config.publishableKey || "").trim();
  const validPublishableKey = key => key.startsWith("sb_publishable_") || key.startsWith("eyJ");

  function clearSession() {
    sessionStorage.removeItem(storage.access);
    sessionStorage.removeItem(storage.refresh);
    sessionStorage.removeItem(storage.expires);
  }

  function saveSession(payload) {
    sessionStorage.setItem(storage.access, payload.access_token);
    sessionStorage.setItem(storage.refresh, payload.refresh_token || "");
    sessionStorage.setItem(storage.expires, String(Date.now() + Number(payload.expires_in || 3600) * 1000));
  }

  async function tokenRequest(grantType, body, key = publishableKey()) {
    if (!validPublishableKey(key)) throw new Error("看板配置缺少有效的 Supabase Publishable Key。");
    const response = await fetch(`${config.supabaseUrl}/auth/v1/token?grant_type=${grantType}`, {
      method: "POST",
      headers: {"Content-Type": "application/json", apikey: key},
      body: JSON.stringify(body)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error_description || payload.msg || "登录失败，请检查邮箱和密码。");
    return payload;
  }

  async function getAccessToken() {
    const token = sessionStorage.getItem(storage.access);
    const expiresAt = Number(sessionStorage.getItem(storage.expires) || 0);
    if (token && (!expiresAt || expiresAt > Date.now() + 60_000)) return token;
    const refreshToken = sessionStorage.getItem(storage.refresh);
    if (!refreshToken || !publishableKey()) {
      clearSession();
      return null;
    }
    try {
      const payload = await tokenRequest("refresh_token", {refresh_token: refreshToken});
      saveSession(payload);
      return payload.access_token;
    } catch {
      clearSession();
      return null;
    }
  }

  function setError(message = "") {
    const node = document.querySelector("#auth-error");
    if (node) node.textContent = message;
  }

  function showGate(message = "") {
    const gate = document.querySelector("#auth-gate");
    if (!gate) return;
    gate.hidden = false;
    document.body.classList.add("auth-locked");
    setError(message);
    requestAnimationFrame(() => document.querySelector("#auth-email")?.focus());
  }

  function hideGate() {
    const gate = document.querySelector("#auth-gate");
    if (gate) gate.hidden = true;
    document.body.classList.remove("auth-locked");
    setError("");
  }

  async function ensureAuthenticated(message = "") {
    const token = await getAccessToken();
    if (token) return token;
    showGate(message);
    if (!pendingPromise) {
      pendingPromise = new Promise(resolve => { pendingResolve = resolve; });
    }
    return pendingPromise;
  }

  async function signOut() {
    const token = sessionStorage.getItem(storage.access);
    const key = publishableKey();
    try {
      if (token && key) {
        await fetch(`${config.supabaseUrl}/auth/v1/logout?scope=local`, {
          method: "POST",
          headers: {apikey: key, Authorization: `Bearer ${token}`}
        });
      }
    } finally {
      clearSession();
      showGate("已安全退出。");
      window.dispatchEvent(new CustomEvent("trend-dashboard-signed-out"));
    }
  }

  document.querySelector("#auth-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const submit = document.querySelector("#auth-submit");
    const email = document.querySelector("#auth-email").value.trim();
    const password = document.querySelector("#auth-password").value;
    setError("");
    submit.disabled = true;
    submit.textContent = "登录中…";
    try {
      const payload = await tokenRequest("password", {email, password});
      saveSession(payload);
      document.querySelector("#auth-password").value = "";
      hideGate();
      const resolve = pendingResolve;
      pendingResolve = null;
      pendingPromise = null;
      if (resolve) {
        resolve(payload.access_token);
      } else {
        window.dispatchEvent(new CustomEvent("trend-dashboard-authenticated", {detail: {accessToken: payload.access_token}}));
      }
    } catch (error) {
      setError(error.message || "登录失败。");
    } finally {
      submit.disabled = false;
      submit.textContent = "登录";
    }
  });

  window.TrendDashboardAuth = {
    ensureAuthenticated,
    getAccessToken,
    publishableKey,
    clearSession,
    signOut,
    showGate
  };
})();
