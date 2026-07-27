/*
 * Trend Observer dashboard runtime.
 *
 * Project endpoints come from dashboard_config.js.  Auth is handled by
 * dashboard_auth.js; only the public publishable key may be stored locally.
 */
(() => {
  "use strict";

  const EXTRA_MOCK_INDICES = [
    {name:"中证A500",code:"000510",date:"2026-07-10",close:5188.42,day:-1.12,pe:16.82,pep:63.2,value:"合理",advice:"可新增",status:"健康上升",short:"短期震荡",mid:"中期上升",long:"长期上升",ytd:7.6,w:-1.1,m:2.8,y:18.2,y3:25.1,ma120:5030.1,ma200:4882.3,s120:1.2,s200:2.3},
    {name:"中证消费",code:"000932",date:"2026-07-10",close:12640.18,day:-0.84,pe:25.41,pep:41.8,value:"合理",advice:"可新增",status:"趋势修复",short:"短期强势",mid:"中期修复",long:"长期上升",ytd:4.1,w:0.3,m:3.5,y:10.4,y3:-2.8,ma120:12320.2,ma200:12110.6,s120:0.7,s200:0.5},
    {name:"全指医药",code:"000991",date:"2026-07-10",close:10442.37,day:-1.36,pe:31.27,pep:34.6,value:"低估",advice:"观察等待",status:"趋势修复",short:"短期震荡",mid:"中期修复",long:"长期修复",ytd:6.9,w:-0.4,m:4.2,y:8.5,y3:-16.2,ma120:10220.4,ma200:10310.7,s120:0.8,s200:-0.2},
    {name:"纳斯达克100",code:"NDX100",date:"2026-07-09",close:24612.8,day:0.31,pe:32.14,pep:84.3,value:"高估",advice:"仅持有",status:"健康上升",short:"短期强势",mid:"中期上升",long:"长期上升",ytd:9.8,w:1.4,m:3.1,y:17.7,y3:61.3,ma120:23210.5,ma200:22480.2,s120:1.4,s200:2.1},
    {name:"标普500",code:"SPX",date:"2026-07-09",close:6942.11,day:0.18,pe:24.63,pep:79.1,value:"高估",advice:"仅持有",status:"健康上升",short:"短期震荡",mid:"中期上升",long:"长期上升",ytd:7.2,w:0.9,m:2.4,y:13.6,y3:44.8,ma120:6720.7,ma200:6540.3,s120:1.0,s200:1.6}
  ];

  const runtimeState = {
    apiBase: "",
    portfolioApiBase: "",
    token: "",
    version: null,
    source: "mock",
    histories: new Map(),
    historyRanges: new Map(),
    detailErrors: new Map(),
    detailRequests: new Map(),
    permissions: new Set(["view_dashboard", "view_fundamentals", "edit_configuration"])
  };
  // Mock is a precomputed fixture. It intentionally contains display results,
  // not browser-side allocation or style rules.
  const MOCK_ALLOCATION = {
    rows: [
      {category:"海外", target_ratio:20, actual_amount:180000, actual_ratio:18, deviation:-2, deviation_state:"均衡", theoretical_adjustment_amount:20000},
      {category:"红利", target_ratio:20, actual_amount:265000, actual_ratio:26.5, deviation:6.5, deviation_state:"明显超配", theoretical_adjustment_amount:-65000},
      {category:"成长", target_ratio:20, actual_amount:245000, actual_ratio:24.5, deviation:4.5, deviation_state:"关注", theoretical_adjustment_amount:-45000},
      {category:"债券", target_ratio:25, actual_amount:170000, actual_ratio:17, deviation:-8, deviation_state:"明显低配", theoretical_adjustment_amount:80000},
      {category:"大宗商品", target_ratio:10, actual_amount:85000, actual_ratio:8.5, deviation:-1.5, deviation_state:"均衡", theoretical_adjustment_amount:15000},
      {category:"现金", target_ratio:5, actual_amount:55000, actual_ratio:5.5, deviation:0.5, deviation_state:"均衡", theoretical_adjustment_amount:-5000}
    ],
    summary: {text:"红利实际占比26.5%，较目标高6.5个百分点；债券低配8.0个百分点，是当前最需要补足的类别。", total_amount:1000000},
    updated_at:null,
    data_date:"2026-07-10"
  };
  const MOCK_COMPASS = [
    {left:"红利低波", right:"创业板100", date:"2026-07-10", score:-100, direction:"偏右", recommendation:"暂不倾斜", recommendationReason:"占优侧投资建议为“仅持有”，未通过新增约束。", leftReturns:[-6.8,-12.4,-18.1], rightReturns:[0,0,0], d:[-6.8,-12.4,-18.1], lp:45.22, rp:null, la:"暂停参与", ra:"仅持有"},
    {left:"国证自由现金流", right:"科创50", date:"2026-07-10", score:-100, direction:"偏右", recommendation:"暂不倾斜", recommendationReason:"占优侧投资建议为“仅持有”，未通过新增约束。", leftReturns:[-8.2,-16.3,-20.5], rightReturns:[0,0,0], d:[-8.2,-16.3,-20.5], lp:28.4, rp:99.65, la:"优先新增", ra:"仅持有"},
    {left:"沪深300", right:"中证500", date:"2026-07-10", score:-59, direction:"偏右", recommendation:"暂不倾斜", recommendationReason:"占优侧投资建议为“仅持有”，未通过新增约束。", leftReturns:[-4.59,-3.83,-6.81], rightReturns:[0,0,0], d:[-4.59,-3.83,-6.81], lp:78.13, rp:82.94, la:"仅持有", ra:"仅持有"}
  ];
  runtimeState.allocation = MOCK_ALLOCATION;
  window.__trendDashboardRuntime = runtimeState;

  const injected = window.TREND_DASHBOARD_CONFIG || {};
  runtimeState.apiBase = String(injected.apiBase || localStorage.getItem("trend-dashboard-api-base") || "").replace(/\/$/, "");
  runtimeState.portfolioApiBase = String(injected.portfolioApiBase || "").replace(/\/$/, "");

  const numberOr = (value, fallback = null) => {
    if (value === null || value === undefined || value === "") return fallback;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const returnPercent = value => {
    const parsed = numberOr(value);
    return parsed === null ? null : parsed * 100;
  };
  const displayCode = symbol => String(symbol || "").replace(/^(sh|sz|hk)(?=\d)/i, "");
  const escapeHtml = value => String(value ?? "—").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  const templateAlias = template => ({
    utility_concession: "utility",
    resource_cycle: "resource",
    durable_manufacturing: "consumer"
  })[template] || template || "consumer";
  const templateAnchor = template => ({
    utility_concession: "公用事业与特许经营",
    resource_cycle: "资源周期",
    durable_manufacturing: "耐用消费制造",
    bank: "银行"
  })[template] || "行业经营";

  function setSourceStatus(source, message, generatedAt) {
    runtimeState.source = source;
    const node = document.querySelector("#dashboard-source");
    if (node) {
      node.className = "source-pill " + (source === "api" ? "api" : source === "error" ? "error" : "mock");
      node.textContent = message;
      node.title = source === "api"
        ? "数据来自已鉴权的 Supabase Edge API"
        : "未配置 API 或认证信息时使用内嵌 Mock，便于本地预览";
    }
    const meta = document.querySelector(".topmeta");
    const date = generatedAt ? new Date(generatedAt).toLocaleString("zh-CN", {hour12:false}) : "2026-07-10";
    if (meta) meta.innerHTML = `<b>数据更新 ${date}</b><br><span id="dashboard-source" class="${node ? node.className : "source-pill mock"}">${message}</span>${source === "api" ? '<button class="source-pill source-pill-button" id="dashboard-logout" type="button">退出</button>' : ""}`;
  }

  function mapAsset(asset, type) {
    const fallback = (type === "index" ? indices : stocks).find(item => item.name === asset.name) || {};
    const assessment = asset.fundamental_assessment || {};
    const delayed = asset.data_status === "delayed";
    const blankDecimal = { toFixed: () => "—", valueOf: () => Number.NaN };
    // API payload is authoritative: missing fields must not inherit fabricated Mock values.
    const currentNumber = value => delayed ? null : numberOr(value);
    const currentText = (value, unavailable = "数据延迟") => delayed ? unavailable : value || "数据不足";
    return {
      ...fallback,
      name: asset.name || fallback.name || asset.symbol,
      code: asset.symbol || fallback.code,
      date: asset.trade_date || fallback.date || "—",
      dataStatus: asset.data_status || "current",
      dataIssue: asset.data_issue || null,
      lastValidDate: asset.last_valid_trade_date || null,
      close: currentNumber(asset.close),
      day: delayed ? null : returnPercent(asset.daily_return),
      pe: currentNumber(asset.pe),
      pep: delayed ? null : numberOr(asset.pe_percentile),
      value: currentText(asset.valuation_status),
      advice: currentText(asset.investment_advice, "暂不判断"),
      status: currentText(asset.overall_status),
      short: currentText(asset.short_trend),
      mid: currentText(asset.mid_trend),
      long: currentText(asset.long_trend),
      ytd: delayed ? null : returnPercent(asset.return_ytd),
      w: delayed ? null : returnPercent(asset.return_1w),
      m: delayed ? null : returnPercent(asset.return_1m),
      y: delayed ? null : returnPercent(asset.return_1y),
      y3: delayed ? null : returnPercent(asset.return_3y),
      ma120: currentNumber(asset.ma120),
      ma200: currentNumber(asset.ma200),
      s120: delayed ? null : returnPercent(asset.ma120_slope_20d),
      s200: delayed ? null : returnPercent(asset.ma200_slope_40d),
      div: numberOr(asset.last_year_dividend) ?? blankDecimal,
      yield: delayed ? blankDecimal : (numberOr(asset.dividend_yield) ?? blankDecimal),
      dividendSafety: assessment.dividend_safety_status || asset.dividend_safety_status || "数据不足",
      fund: assessment.fundamental_status || asset.fundamental_status || "数据不足",
      period: assessment.report_period || "数据不足",
      announce: asset.latest_announcement_date || "数据不足",
      template: templateAlias(asset.industry_template) || fallback.template,
      sourceTemplate: asset.industry_template,
      anchor: fallback.anchor || templateAnchor(asset.industry_template),
      change: assessment.main_risk || fallback.change || "尚无稳定的结构化基本面结论。",
      assessment
    };
  }

  function applyOverview(payload) {
    runtimeState.permissions = new Set(Array.isArray(payload.permissions) ? payload.permissions : ["view_dashboard", "view_fundamentals", "edit_configuration"]);
    const assets = Array.isArray(payload.assets) ? payload.assets : [];
    const apiIndices = assets.filter(item => item.asset_type === "指数").map(item => mapAsset(item, "index"));
    const apiStocks = assets.filter(item => item.asset_type === "股票").map(item => mapAsset(item, "stock"));
    if (apiIndices.length !== 12 || apiStocks.length !== 9) {
      throw new Error(`资产清单不完整：指数 ${apiIndices.length}/12，股票 ${apiStocks.length}/9`);
    }
    const indexOrder = ["沪深300", "中证500", "中证A500", "创业板100", "科创50", "红利低波", "国证自由现金流", "恒生指数", "中证消费", "全指医药", "标普500", "纳斯达克100"];
    const indexRank = new Map(indexOrder.map((name, index) => [name, index]));
    apiIndices.sort((left, right) => (indexRank.get(left.name) ?? Number.MAX_SAFE_INTEGER) - (indexRank.get(right.name) ?? Number.MAX_SAFE_INTEGER));
    indices.splice(0, indices.length, ...apiIndices);
    stocks.splice(0, stocks.length, ...apiStocks);

    const allocation = payload.allocation && Array.isArray(payload.allocation.rows) ? payload.allocation : null;
    const rows = allocation ? allocation.rows : [];
    if (rows.length === categories.length) {
      const colors = new Map(categories.map(item => [item.name, item.color]));
      categories.splice(0, categories.length, ...rows.map(row => ({
        name: row.category,
        color: colors.get(row.category),
        target: numberOr(row.target_ratio, 0),
        actual: numberOr(row.actual_amount, 0),
        actualRatio: numberOr(row.actual_ratio, 0),
        deviation: numberOr(row.deviation, 0),
        deviationState: row.deviation_state || "数据不足",
        adjustment: numberOr(row.theoretical_adjustment_amount, 0)
      })));
    }

    runtimeState.allocation = allocation;
    const allocationUpdated = allocation && allocation.updated_at;
    const allocationDataDate = allocation && allocation.data_date;
    const allocationNode = document.querySelector("#allocation-updated-at");
    if (allocationNode) {
      const updated = allocationUpdated ? new Date(allocationUpdated).toLocaleString("zh-CN", {hour12:false}) : "—";
      allocationNode.textContent = `配置更新于 ${updated}${allocationDataDate ? ` · 数据日期 ${allocationDataDate}` : ""}`;
    }

    const compass = Array.isArray(payload.style_compass) ? payload.style_compass : [];
    if (compass.length) {
      pairs.splice(0, pairs.length, ...compass.map(item => ({
        left: item.left && item.left.name,
        right: item.right && item.right.name,
        date: item.as_of_date || "—",
        leftReturns: [20, 60, 120].map(window => returnPercent(item[`return_${window}d_left`])),
        rightReturns: [20, 60, 120].map(window => returnPercent(item[`return_${window}d_right`])),
        d: [20, 60, 120].map(window => returnPercent(item[`return_${window}d_diff`]) ?? 0),
        score: numberOr(item.score),
        direction: item.direction || "数据不足",
        recommendation: item.recommendation || "数据不足",
        recommendationReason: item.recommendation_reason || "服务端未提供风格建议说明。",
        lp: numberOr(item.left && item.left.pe_percentile),
        rp: numberOr(item.right && item.right.pe_percentile),
        la: item.left && item.left.investment_advice || "数据不足",
        ra: item.right && item.right.investment_advice || "数据不足"
      })));
    }
    runtimeState.version = payload.version || null;
    runtimeState.completeness = payload.completeness || {};
  }

  function renderAll() {
    renderAllocation();
    renderCompass();
    renderFocus();
    renderIndex();
    renderStock();
  }

  const CATEGORY_ORDER = ["海外", "红利", "成长", "债券", "大宗商品", "现金"];
  const categoryRank = new Map(CATEGORY_ORDER.map((name, index) => [name, index]));
  let allocationSort = { key: "category", direction: "asc" };

  renderAllocation = function renderAllocationWithConfiguredOrder() {
    const sourceRows = runtimeState.allocation && Array.isArray(runtimeState.allocation.rows)
      ? runtimeState.allocation.rows : [];
    const colors = new Map(categories.map(category => [category.name, category.color]));
    const rows = sourceRows.map(row => ({
      name: row.category,
      color: colors.get(row.category),
      target: numberOr(row.target_ratio, 0),
      actual: numberOr(row.actual_ratio, 0),
      deviation: numberOr(row.deviation, 0),
      state: row.deviation_state || "数据不足",
      adjust: numberOr(row.theoretical_adjustment_amount, 0)
    }));
    if (rows.length !== CATEGORY_ORDER.length) {
      document.querySelector("#alloc-summary").textContent = "配置数据不足，暂不展示偏离结论。";
      return;
    }
    const stack = (selector, values) => {
      document.querySelector(selector).innerHTML = rows.map((category, index) =>
        `<div class="stack-seg" data-cat="${category.name}" data-allocation-tooltip="${category.name} ${values[index].toFixed(1)}%" style="width:${values[index]}%"><span>${values[index].toFixed(1)}%</span></div>`
      ).join("");
    };
    stack("#target-stack", rows.map(category => category.target));
    stack("#actual-stack", rows.map(row => row.actual));
    bindAllocationTooltips();
    document.querySelector("#alloc-legend").innerHTML = rows.map(category => `<span><i style="background:${category.color}"></i>${category.name}</span>`).join("");
    const sorted = [...rows].sort((left, right) => {
      if (allocationSort.key === "deviation") {
        return (Math.abs(right.deviation) - Math.abs(left.deviation)) * (allocationSort.direction === "asc" ? -1 : 1);
      }
      return (categoryRank.get(left.name) ?? Number.MAX_SAFE_INTEGER) - (categoryRank.get(right.name) ?? Number.MAX_SAFE_INTEGER);
    });
    document.querySelectorAll("[data-allocation-sort]").forEach(button => {
      const active = allocationSort.key === "deviation";
      button.textContent = `偏离 ${active ? (allocationSort.direction === "asc" ? "↑" : "↓") : "↕"}`;
      button.classList.toggle("active", active);
    });
    document.querySelector("#allocation-body").innerHTML = sorted.map(row => {
      const stateClass = /^明显/.test(row.state) ? "risk" : row.state === "关注" ? "warn" : "info";
      return `<tr><td><i class="category-dot" style="background:${row.color}"></i>${row.name}</td><td class="num">${row.target.toFixed(1)}%</td><td class="num">${row.actual.toFixed(1)}%</td><td class="num ${row.deviation > 0 ? "pos" : row.deviation < 0 ? "neg" : ""}">${row.deviation > 0 ? "+" : ""}${row.deviation.toFixed(1)}pp</td><td><span class="badge ${stateClass}">${row.state}</span></td><td class="num ${row.adjust > 0 ? "pos" : row.adjust < 0 ? "neg" : ""}">${row.adjust > 0 ? "+" : ""}${money(row.adjust)}</td></tr>`;
    }).join("");
    const summary = document.querySelector("#alloc-summary");
    if (summary) summary.textContent = runtimeState.allocation?.summary?.text || "配置数据不足，暂不展示偏离结论。";
  };

  function bindAllocationTooltips() {
    const tooltipId = "dashboard-allocation-tooltip";
    const hide = () => document.querySelector(`#${tooltipId}`)?.remove();
    document.querySelectorAll("[data-allocation-tooltip]").forEach(segment => {
      const show = event => {
        let tooltip = document.querySelector(`#${tooltipId}`);
        if (!tooltip) {
          tooltip = document.createElement("div");
          tooltip.id = tooltipId;
          tooltip.className = "chart-tooltip";
          document.body.appendChild(tooltip);
        }
        tooltip.textContent = segment.dataset.allocationTooltip || "";
        tooltip.style.left = `${Math.min(event.clientX + 14, window.innerWidth - 180)}px`;
        tooltip.style.top = `${Math.min(event.clientY + 14, window.innerHeight - 60)}px`;
      };
      segment.onpointerenter = show;
      segment.onpointermove = show;
      segment.onpointerleave = hide;
    });
  }

  function updateTargetTotalHint() {
    const hint = document.querySelector("#target-total");
    const values = [...document.querySelectorAll("[data-edit-index]")].map(input => numberOr(input.value, 0));
    const total = values.reduce((sum, value) => sum + value, 0);
    const valid = Math.abs(total - 100) < 1e-8;
    if (hint) {
      hint.textContent = `目标配置合计：${total.toFixed(1)}%${valid ? "" : "（必须等于100%）"}`;
      hint.classList.toggle("invalid", !valid);
    }
    return valid;
  }

  openEdit = function openAllocationEditor(mode) {
    editMode = mode;
    document.querySelector("#edit-title").textContent = mode === "target" ? "编辑目标比例" : "编辑实际金额";
    const today = new Date().toLocaleDateString("en-CA", {timeZone:"Asia/Shanghai"});
    const hint = mode === "target" ? '<div class="target-total" id="target-total"></div>' : "";
    document.querySelector("#edit-grid").innerHTML = categories.map((category, index) =>
      `<div class="edit-row"><label><i class="category-dot" style="background:${category.color}"></i>${category.name}</label><input type="number" min="0" ${mode === "target" ? 'max="100" step="0.1"' : 'step="0.01"'} data-edit-index="${index}" value="${mode === "target" ? category.target : category.actual}"></div>`
    ).join("") + hint + `<div class="edit-row"><label>数据日期</label><input type="date" id="edit-date" value="${today}"></div>`;
    if (mode === "target") {
      document.querySelectorAll("[data-edit-index]").forEach(input => input.addEventListener("input", updateTargetTotalHint));
      updateTargetTotalHint();
    }
    document.querySelector("#edit-error").textContent = "";
    document.querySelector("#edit-modal").showModal();
  };

  async function saveAllocationEditor() {
    const inputs = [...document.querySelectorAll("[data-edit-index]")];
    const rawValues = inputs.map(input => Number(input.value));
    const errorNode = document.querySelector("#edit-error");
    const date = document.querySelector("#edit-date").value;
    const button = document.querySelector("#save-edit");
    const precision = editMode === "target" ? 1 : 2;
    const scale = 10 ** precision;
    if (!date) return void (errorNode.textContent = "请选择数据日期。");
    if (rawValues.some(value => !Number.isFinite(value) || value < 0)) return void (errorNode.textContent = "请填写有效的非负数值。");
    if (editMode === "target" && rawValues.some(value => value > 100 || Math.abs(value * scale - Math.round(value * scale)) > 1e-8)) return void (errorNode.textContent = "目标配置比例须为0至100之间的数值，最多1位小数。");
    if (editMode === "actual" && rawValues.some(value => Math.abs(value * scale - Math.round(value * scale)) > 1e-8)) return void (errorNode.textContent = "实际配置金额须为非负数，最多2位小数。");
    const values = rawValues.map(value => Number(value.toFixed(precision)));
    const total = values.reduce((sum, value) => sum + value, 0);
    if (editMode === "target" && Math.abs(total - 100) > 1e-8) return void (errorNode.textContent = `目标配置比例合计须等于100%，当前为${total.toFixed(1)}%。`);
    if (editMode === "actual" && total <= 0) return void (errorNode.textContent = "至少一个类别的实际金额须大于0。");
    try {
      button.disabled = true; button.textContent = "保存中…"; errorNode.textContent = "";
      const result = await saveAllocation(editMode === "target" ? "target_ratio" : "actual_amount", date, categories.map((category, index) => ({category:category.name, value:values[index]})));
      if (!result.allocation || !Array.isArray(result.allocation.rows)) throw new Error("allocation_compute_failed");
      runtimeState.allocation = result.allocation;
      const savedRows = new Map(result.allocation.rows.map(row => [row.category, row]));
      categories.forEach(category => {
        const row = savedRows.get(category.name);
        if (!row) return;
        category.target = numberOr(row.target_ratio, category.target);
        category.actual = numberOr(row.actual_amount, category.actual);
        category.actualRatio = numberOr(row.actual_ratio, category.actualRatio);
        category.deviation = numberOr(row.deviation, category.deviation);
        category.deviationState = row.deviation_state || category.deviationState;
        category.adjustment = numberOr(row.theoretical_adjustment_amount, category.adjustment);
      });
      renderAllocation(); document.querySelector("#edit-modal").close();
    } catch (error) {
      errorNode.textContent = /Failed to fetch/i.test(error.message || "")
        ? "本地文件预览无法连接 Supabase；请在正式 HTTPS 页面保存配置。"
        : (error.message || "保存失败，请稍后重试。");
    } finally {
      button.disabled = false; button.textContent = "确定";
    }
  }

  document.addEventListener("click", event => {
    if (!event.target.closest("#save-edit")) return;
    event.preventDefault(); event.stopImmediatePropagation();
    saveAllocationEditor();
  }, true);

  renderCompass = function renderCompassWithActualDates() {
    document.querySelector("#compass-grid").innerHTML = pairs.map(pair => {
      const score = numberOr(pair.score, 0), direction = pair.direction || "数据不足";
      const recommendation = pair.recommendation || "数据不足";
      const why = pair.recommendationReason || "服务端未提供风格建议说明。";
      const lefts = pair.leftReturns || [null, null, null], rights = pair.rightReturns || [null, null, null];
      return `<article class="panel compass"><div class="pair-head"><div class="pair-title">${pair.left} <span class="muted">vs</span> ${pair.right}</div><div class="pair-date">${pair.date || "—"}</div></div><div class="score-line"><div class="score-track"></div><div class="score-dot" style="left:${(score + 100) / 2}%"></div><div class="score-labels"><span>${pair.left}</span><span>均衡</span><span>${pair.right}</span></div></div><div class="score-box"><div><div class="score-value">${score}</div><small class="muted">综合分数</small></div><div class="direction"><b>${direction}</b><span>原始风格方向</span></div></div><div class="periods"><div class="period-row header"><span>周期</span><span>左侧</span><span>右侧</span><span>收益差</span></div>${[20, 60, 120].map((window, index) => `<div class="period-row"><span>${window}日</span><span>${pct(lefts[index])}</span><span>${pct(rights[index])}</span><span class="${pair.d[index] > 0 ? "pos" : pair.d[index] < 0 ? "neg" : ""}">${pct(pair.d[index])}</span></div>`).join("")}</div><div class="pair-facts"><div class="pair-side"><b>${pair.left}</b><div class="fact"><span>PE百分位</span><strong>${pair.lp == null ? "—" : pair.lp.toFixed(2) + "%"}</strong></div><div class="fact"><span>投资建议</span><strong>${pair.la}</strong></div></div><div class="pair-side"><b>${pair.right}</b><div class="fact"><span>PE百分位</span><strong>${pair.rp == null ? "—" : pair.rp.toFixed(2) + "%"}</strong></div><div class="fact"><span>投资建议</span><strong>${pair.ra}</strong></div></div></div><div class="recommend"><b>${recommendation}</b><small>${why}</small></div></article>`;
    }).join("");
  };

  document.addEventListener("click", event => {
    const button = event.target.closest("[data-allocation-sort]");
    if (!button) return;
    allocationSort = allocationSort.key === "deviation"
      ? {key: "deviation", direction: allocationSort.direction === "desc" ? "asc" : "desc"}
      : {key: "deviation", direction: "desc"};
    renderAllocation();
  });

  const originalRenderFocus = renderFocus;
  renderFocus = function renderDynamicFocus() {
    if (!indices.length || !stocks.length) return originalRenderFocus();
    const focusStatuses = new Set(["强趋势", "下跌通道"]);
    const isAvailable = asset => asset.dataStatus !== "delayed"
      && Number.isFinite(asset.close)
      && Number.isFinite(asset.day)
      && !["数据不足", "数据延迟", "—"].includes(asset.status)
      && !["数据不足", "数据延迟", "—"].includes(asset.mid)
      && !["数据不足", "数据延迟", "—"].includes(asset.long);
    const candidates = [
      ...indices.filter(asset => isAvailable(asset) && Number.isFinite(asset.pep))
        .filter(asset => asset.pep <= 35 || asset.pep >= 70 || focusStatuses.has(asset.status))
        .map(asset => ({type: "index", asset, cls: asset.pep >= 70 || asset.status === "下跌通道" ? "risk" : "good"})),
      ...stocks.filter(asset => isAvailable(asset) && Number.isFinite(asset.yield))
        .filter(asset => asset.yield <= 3 || asset.yield >= 5 || focusStatuses.has(asset.status))
        .map(asset => ({type: "stock", asset, cls: asset.yield <= 3 || asset.status === "下跌通道" ? "risk" : "good"}))
    ];
    const countNode = document.querySelector("#focus-count");
    if (countNode) countNode.textContent = `共 ${candidates.length} 项`;
    document.querySelector("#focus-grid").innerHTML = candidates.map(item => {
      const list = item.type === "index" ? indices : stocks;
      const i = list.indexOf(item.asset);
      const valuationMetric = item.type === "index"
        ? `<div><small>PE百分位</small><b>${item.asset.pep.toFixed(2)}%</b></div>`
        : `<div><small>股息率</small><b>${item.asset.yield.toFixed(2)}%</b></div>`;
      const statusMetric = focusStatuses.has(item.asset.status)
        ? `<div><small>综合状态</small><b>${escapeHtml(item.asset.status)}</b></div>`
        : "";
      return `<button class="focus-card ${item.cls}" data-focus-type="${item.type}" data-focus-index="${i}"><div class="focus-top"><div><div class="focus-name">${escapeHtml(item.asset.name)}</div><div class="focus-code">${escapeHtml(displayCode(item.asset.code))} · ${item.type === "index" ? "指数" : "股票"}</div></div><div class="focus-price"><b>${num(item.asset.close)}</b><div class="${item.asset.day > 0 ? "pos" : "neg"}">${pct(item.asset.day)}</div></div></div><div class="focus-metrics">${valuationMetric}${statusMetric}<div><small>中期趋势</small><b>${escapeHtml(item.asset.mid)}</b></div><div><small>长期趋势</small><b>${escapeHtml(item.asset.long)}</b></div></div></button>`;
    }).join("") || '<div class="data-missing">当前没有满足优先关注条件且数据完整的资产。</div>';
  };

  const originalProfileFor = profileFor;
  profileFor = function profileFromAssessment(asset) {
    const profile = originalProfileFor(asset);
    const assessment = asset.assessment || {};
    if (!assessment.report_period) return profile;
    profile.pillars = [
      assessment.dividend_safety_status || asset.dividendSafety,
      assessment.operating_quality_status || asset.fund,
      assessment.cash_reinvestment_status || "数据不足",
      assessment.capital_structure_status || "数据不足"
    ];
    profile.summary = assessment.fundamental_status || "数据不足";
    const evidence = Array.isArray(assessment.evidence) ? assessment.evidence : [];
    profile.evidence = evidence.length
      ? evidence.map(item => typeof item === "string" ? item : item.summary || item.metric_code || JSON.stringify(item)).slice(0, 5)
      : ["尚无可展示的结构化研究证据。"];
    if (assessment.main_risk) profile.evidence.push(`主要风险：${assessment.main_risk}`);
    return profile;
  };

  const originalAnnualResearch = annualResearch;
  annualResearch = function annualResearchFromApi(asset) {
    const assessment = asset.assessment || {};
    const facts = Array.isArray(asset.financialFacts) ? asset.financialFacts : [];
    const dividends = Array.isArray(asset.dividendEvents) ? asset.dividendEvents : [];
    const industry = Array.isArray(asset.industryMetrics) ? asset.industryMetrics : [];
    const sources = Array.isArray(asset.sources) ? asset.sources : [];
    if (runtimeState.source === "api" && !assessment.report_period) {
      return '<section class="research-section"><h4>高股息研究摘要</h4><div class="data-missing">数据不足：尚未接入该股票的结构化财报、分红或行业指标。为避免误导，不展示原型或估算数值。</div></section>';
    }
    if (runtimeState.source === "api" && assessment.report_period) {
      const profile = profileFor(asset);
      const factRows = facts.slice(0, 60).map(row => `<tr><td>${escapeHtml(row.report_period)}</td><td>${escapeHtml(row.metric_code)}</td><td class="num">${escapeHtml(row.value)}</td><td>${escapeHtml(row.unit)}</td><td>${escapeHtml(row.period_type)}</td><td>${escapeHtml(row.announcement_date)}</td></tr>`).join("");
      const dividendRows = dividends.slice(0, 12).map(row => `<tr><td>${escapeHtml(row.fiscal_year)}</td><td>${escapeHtml(row.event_stage)}</td><td class="num">${escapeHtml(row.cash_dividend_per_share)}</td><td>${escapeHtml(row.announcement_date)}</td><td>${escapeHtml(row.announcement_id)}</td></tr>`).join("");
      const industryRows = industry.slice(0, 24).map(row => `<div class="anchor-item"><small>${escapeHtml(row.metric_code)} · ${escapeHtml(row.period)}</small><b>${escapeHtml(row.value)} ${escapeHtml(row.unit)}</b></div>`).join("");
      const sourceRows = sources.slice(0, 12).map(row => `<li>${row.document_url ? `<a href="${escapeHtml(row.document_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.title || row.source_record_id)}</a>` : escapeHtml(row.title || row.source_record_id)} · ${escapeHtml(row.source)} · ${escapeHtml(row.announcement_date)} · hash ${escapeHtml(String(row.content_hash || "").slice(0, 12))}</li>`).join("");
      return `<div class="research-header"><div><h4>高股息研究摘要</h4><p>${escapeHtml(asset.anchor)} · 不将研究摘要直接转化为交易建议</p></div><div class="research-meta"><span>最新报告期 ${escapeHtml(assessment.report_period)}</span><span>计算版本 ${escapeHtml(assessment.calculation_version)}</span><span>数据完整度：结构化事实</span></div></div><div class="pillar-grid">${statusCard("分红保障", profile.pillars[0])}${statusCard("经营质量", profile.pillars[1])}${statusCard("现金与再投资", profile.pillars[2])}${statusCard("资本结构", profile.pillars[3])}</div><div class="research-conclusion"><b>研究摘要 · ${escapeHtml(profile.summary)}</b><ul class="research-evidence">${profile.evidence.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div><section class="research-section"><h4>年度与 TTM 结构化指标</h4><div class="historical-wrap"><table><thead><tr><th>报告期</th><th>指标</th><th class="num">数值</th><th>单位</th><th>口径</th><th>公告日期</th></tr></thead><tbody>${factRows || '<tr><td colspan="6">数据不足：财务事实适配器尚未产出稳定数据。</td></tr>'}</tbody></table></div></section><section class="research-section"><h4>分红事件</h4><div class="historical-wrap"><table><thead><tr><th>年度</th><th>阶段</th><th class="num">每股分红</th><th>公告日期</th><th>公告编号</th></tr></thead><tbody>${dividendRows || '<tr><td colspan="5">数据不足</td></tr>'}</tbody></table></div></section><section class="research-section"><h4>行业经营锚点 · ${escapeHtml(asset.anchor)}</h4><div class="industry-anchor">${industryRows || '<div class="data-missing">暂无已确认的行业指标；待适配器接入后展示。</div>'}</div></section><section class="research-section"><h4>来源与追溯</h4><div class="chart-box"><ul class="research-evidence">${sourceRows || "<li>暂无来源元数据。</li>"}</ul><p class="research-source">仅展示来源链接、公告编号、内容哈希、抓取时间及结构化字段；不保存原始公告、财报文件或接口响应全文。</p></div></section>`;
    }
    let html = originalAnnualResearch(asset);
    return html.replace("数据完整度：原型示例", "数据完整度：Mock 示例");
  };

  const originalStockDetail = stockDetail;
  stockDetail = function stockDetailFromApi(asset) {
    const ranges = [[3,"近3个月"],[6,"近6个月"],[12,"近1年"],[36,"近3年"],[60,"近5年"]];
    const movingAverages = [["ma20","MA20"],["ma60","MA60"],["ma120","MA120"],["ma200","MA200"]];
    const trends = [["short","短期"],["mid","中期"],["long","长期"]];
    const yieldText = Number.isFinite(Number(asset.yield)) ? `${Number(asset.yield).toFixed(2)}%` : "—";
    const dividendText = Number.isFinite(Number(asset.div)) ? `${Number(asset.div).toFixed(2)} 元/股` : "—";
    const pricePane = `<div class="modal-pane active" id="pane-price"><div class="chart-box"><div class="chart-title"><h4>价格与趋势</h4><small>趋势带默认仅展示长期</small></div><div class="chart-control-bar"><div class="chart-controls"><span class="control-label">时间范围</span>${ranges.map(([value, label]) => `<button class="chart-chip ${detailRange === value ? "active" : ""}" data-range="${value}">${label}</button>`).join("")}</div><div class="chart-controls"><span class="control-label">均线</span>${movingAverages.map(([value, label]) => `<button class="chart-chip ${selectedMAs.has(value) ? "active" : ""}" data-ma="${value}">${label}</button>`).join("")}<span class="control-label">趋势带</span>${trends.map(([value, label]) => `<button class="chart-chip ${selectedTrendBands.has(value) ? "active" : ""}" data-trend-band="${value}">${label}</button>`).join("")}</div></div><div class="chart-legend-row"><div class="chart-legend"><span><i style="background:#172b3a"></i>收盘</span><span><i style="background:#75a7d8"></i>MA20</span><span><i style="background:#8fc4b5"></i>MA60</span><span><i style="background:#b09bc9"></i>MA120</span><span><i style="background:#d7a68f"></i>MA200</span></div><div class="trend-key"><span><i style="background:#9fcfb7"></i>上升</span><span><i style="background:#aebfe2"></i>修复</span><span><i style="background:#e2c891"></i>转弱</span><span><i style="background:#e4aaa7"></i>下跌</span><span><i style="background:#cfd5da"></i>震荡</span></div></div><canvas id="price-chart"></canvas></div></div>`;
    const assessment = asset.assessment || {};
    const sourceRows = Array.isArray(asset.sources) ? asset.sources : [];
    const sourceList = sourceRows.length
      ? `<ul class="research-evidence">${sourceRows.slice(0, 12).map(row => `<li>${row.document_url ? `<a href="${escapeHtml(row.document_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.title || row.source_record_id)}</a>` : escapeHtml(row.title || row.source_record_id)} · ${escapeHtml(row.announcement_date)}</li>`).join("")}</ul>`
      : '<div class="data-missing">暂无已保存的结构化来源元数据。</div>';
    const canViewFundamentals = runtimeState.permissions.has("view_fundamentals");
    const fundamentals = canViewFundamentals
      ? `<button class="modal-tab" data-pane="annual">基本面—年度</button><button class="modal-tab" data-pane="quarter">基本面—季度</button><button class="modal-tab" data-pane="source">数据说明</button>`
      : "";
    const fundamentalPanes = canViewFundamentals
      ? `<div class="modal-pane" id="pane-annual">${annualResearch(asset)}</div><div class="modal-pane" id="pane-quarter"><div class="data-missing">季度基本面口径尚未统一，暂不展示未经确认的原型数字。</div></div><div class="modal-pane" id="pane-source"><div class="chart-box"><p>行情数据截至：${escapeHtml(asset.lastValidDate || asset.date)}</p><p>最新报告期：${escapeHtml(assessment.report_period || "数据不足")}</p><p>数据来源与追溯：</p>${sourceList}<p class="research-source">仅展示来源链接、公告编号、哈希及结构化字段；不保存原始公告、财报文件或接口响应全文。</p></div></div>`
      : "";
    return `<div class="overview-grid">${metric("趋势建议", badge(asset.advice))}${metric("综合状态", badge(asset.status))}${metric("股息率", yieldText)}${metric("分红保障", badge(asset.dividendSafety))}${metric("基本面状态", badge(asset.fund))}${metric("最新报告期", assessment.report_period || "数据不足")}${metric("上一年度每股分红", dividendText)}${metric("主要变化", asset.change || "数据不足")}</div><div class="modal-tabs"><button class="modal-tab active" data-pane="price">价格与趋势</button>${fundamentals}</div>${pricePane}${fundamentalPanes}`;
  };

  const originalIndexDetail = indexDetail;
  indexDetail = function indexDetailFromApi(asset) {
    let html = originalIndexDetail(asset);
    html = html.replace("原型曲线为交互示意，实际版本需展示真实历史数据、数据来源和抓取时间。", "");
    if (runtimeState.histories.has(asset.code)) {
      html = html.replace("当前曲线来自 Edge API 返回的真实结构化历史数据。", "");
    }
    return html;
  };

  async function apiFetch(path) {
    const auth = window.TrendDashboardAuth;
    const token = await auth.getAccessToken();
    const key = auth.publishableKey();
    if (!token || !key) throw new Error("unauthorized");
    runtimeState.token = token;
    const response = await fetch(runtimeState.apiBase + path, {
      headers: {Authorization: `Bearer ${token}`, apikey: key, Accept: "application/json"}
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }

  async function saveAllocation(allocationType, dataDate, values) {
    const auth = window.TrendDashboardAuth;
    const token = await auth.getAccessToken();
    const key = auth.publishableKey();
    if (!runtimeState.portfolioApiBase || !token || !key) throw new Error("unauthorized");
    const response = await fetch(runtimeState.portfolioApiBase, {
      method: "POST",
      headers: {Authorization: `Bearer ${token}`, apikey: key, "Content-Type": "application/json"},
      body: JSON.stringify({allocation_type: allocationType, data_date: dataDate, values})
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) {
        auth.clearSession();
        auth.showGate("登录已失效，请重新登录。");
      }
      if (response.status === 403) throw new Error("当前账号没有 editor 权限。");
      throw new Error(body.error || `HTTP ${response.status}`);
    }
    return body;
  }

  runtimeState.saveAllocation = saveAllocation;

  const rangeKey = months => ({3:"3m",6:"6m",12:"1y",36:"3y",60:"5y"})[months] || "1y";
  const detailRequestKey = (asset, months) => `${asset.code}:${rangeKey(months)}`;
  const hasLoadedDetailRange = (asset, months = detailRange) => runtimeState.historyRanges.get(asset.code) === rangeKey(months);
  const isDetailLoading = (asset, months = detailRange) => {
    const key = detailRequestKey(asset, months);
    return runtimeState.detailRequests.has(key) || (!hasLoadedDetailRange(asset, months) && !runtimeState.detailErrors.has(key));
  };
  async function loadAssetDetail(asset, months) {
    if (runtimeState.source !== "api") return;
    const key = detailRequestKey(asset, months);
    if (runtimeState.detailRequests.has(key)) return runtimeState.detailRequests.get(key);
    const request = apiFetch(`/asset/${encodeURIComponent(asset.code)}?range=${rangeKey(months)}`)
      .then(detail => {
        runtimeState.histories.set(asset.code, detail);
        runtimeState.historyRanges.set(asset.code, rangeKey(months));
        runtimeState.detailErrors.delete(key);
        if (detail.fundamental_assessment) {
          asset.assessment = detail.fundamental_assessment;
          asset.dividendSafety = detail.fundamental_assessment.dividend_safety_status || asset.dividendSafety;
          asset.fund = detail.fundamental_assessment.fundamental_status || asset.fund;
          asset.period = detail.fundamental_assessment.report_period || asset.period;
          asset.change = detail.fundamental_assessment.main_risk || asset.change;
        }
        asset.financialFacts = detail.financial_facts || [];
        asset.dividendEvents = detail.dividend_events || [];
        asset.industryMetrics = detail.industry_metrics || [];
        asset.sources = detail.sources || [];
        return detail;
      })
      .catch(error => {
        runtimeState.detailErrors.set(key, error);
        throw error;
      })
      .finally(() => runtimeState.detailRequests.delete(key));
    runtimeState.detailRequests.set(key, request);
    return request;
  }

  const originalOpenDetail = openDetail;
  openDetail = function openDetailWithApi(type, index) {
    originalOpenDetail(type, index);
    const list = type === "index" ? indices : stocks;
    const asset = list[Number(index)];
    loadAssetDetail(asset, detailRange).then(() => {
      if (detailType === type && detailIndex === Number(index)) originalOpenDetail(type, index);
    }).catch(error => {
      console.warn("详情数据加载失败，保留当前摘要：", error);
    });
  };

  const mockDrawPriceChart = drawPriceChart;
  drawPriceChart = function drawApiPriceChart(asset, type) {
    const detail = hasLoadedDetailRange(asset) ? runtimeState.histories.get(asset.code) : null;
    const historyRows = detail && Array.isArray(detail.history) ? detail.history : [];
    const canvas = document.querySelector("#price-chart");
    if (!canvas) return;
    if (historyRows.length < 2) {
      if (runtimeState.source === "api") {
        return isDetailLoading(asset)
          ? drawLoadingChart(canvas, "正在加载")
          : drawUnavailableChart(canvas, "暂无可用的真实价格历史数据");
      }
      mockDrawPriceChart(asset, type);
      bindChartTooltip(canvas, [], () => [
        `数据：本地 Mock · ${asset.date || "—"}`,
        `收盘：${num(numberOr(asset.close))}`,
        `短期趋势：${asset.short || "—"}`,
        `中期趋势：${asset.mid || "—"}`,
        `长期趋势：${asset.long || "—"}`
      ]);
      return;
    }
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const c = canvas.getContext("2d"); c.scale(dpr, dpr); c.clearRect(0, 0, w, h);
    const rows = historyRows.filter(row => numberOr(row.close) !== null);
    if (rows.length < 2) return drawUnavailableChart(canvas, "暂无可用的真实收盘价数据");
    const left = 60, right = w - 14;
    const lanes = [
      {key:"short", label:"短期", value:"short_trend"},
      {key:"mid", label:"中期", value:"mid_trend"},
      {key:"long", label:"长期", value:"long_trend"}
    ].filter(lane => selectedTrendBands.has(lane.key));
    const top = lanes.length ? lanes.length * 16 + 16 : 18, bottom = h - 36;
    const keys = {ma20:"#75a7d8",ma60:"#8fc4b5",ma120:"#b09bc9",ma200:"#d7a68f"};
    const values = rows.flatMap(row => [numberOr(row.close), ...[...selectedMAs].map(key => numberOr(row[key]))]).filter(value => value !== null);
    const min = Math.min(...values) * .98, max = Math.max(...values) * 1.02;
    const x = i => left + i * (right-left) / Math.max(rows.length-1, 1);
    const y = value => top + (max-value) * (bottom-top) / Math.max(max-min, 1e-9);
    const stateColor = state => /上升|强势/.test(state || "") ? "#9fcfb7" : /修复/.test(state || "") ? "#aebfe2" : /转弱/.test(state || "") ? "#e2c891" : /下跌/.test(state || "") ? "#e4aaa7" : "#cfd5da";
    c.font="10px sans-serif";
    lanes.forEach((lane, laneIndex) => {
      const laneTop = 6 + laneIndex * 16, segmentWidth = (right - left) / rows.length;
      c.fillStyle="#687684"; c.fillText(lane.label, 6, laneTop + 9);
      rows.forEach((row, index) => { c.fillStyle = stateColor(row[lane.value]); c.fillRect(left + index * segmentWidth, laneTop, Math.max(1, segmentWidth), 10); });
    });
    c.strokeStyle="#e5eaee"; c.lineWidth=1;
    for(let i=0;i<5;i++){const gy=top+i*(bottom-top)/4;c.beginPath();c.moveTo(left,gy);c.lineTo(right,gy);c.stroke();}
    const stroke = (key,color,width) => {
      c.strokeStyle=color;c.lineWidth=width;c.beginPath();let started=false;
      rows.forEach((row,i)=>{const value=key==="close"?numberOr(row.close):numberOr(row[key]);if(value===null)return;if(started)c.lineTo(x(i),y(value));else{c.moveTo(x(i),y(value));started=true;}});
      c.stroke();
    };
    stroke("close","#172b3a",2); selectedMAs.forEach(key=>stroke(key,keys[key],1.5));
    c.fillStyle="#687684";c.font="10px sans-serif";
    c.textAlign="left";c.fillText(rows[0].trade_date,left,h-8);
    c.textAlign="right";c.fillText(rows[rows.length-1].trade_date,right,h-8);
    c.textAlign="left";
    bindChartTooltip(canvas, rows, event => {
      const rect = canvas.getBoundingClientRect();
      const index = Math.max(0, Math.min(rows.length - 1, Math.round((event.clientX - rect.left - left) / Math.max(right - left, 1) * (rows.length - 1))));
      const row = rows[index];
      const lines = [`日期：${row.trade_date}`, `收盘：${num(numberOr(row.close))}`];
      [...selectedMAs].forEach(key => lines.push(`${key.toUpperCase()}：${num(numberOr(row[key]))}`));
      lanes.forEach(lane => lines.push(`${lane.label}趋势：${row[lane.value] || "—"}`));
      return lines;
    });
  };

  const mockDrawPeChart = drawPeChart;
  drawPeChart = function drawApiPeChart(asset) {
    const detail = hasLoadedDetailRange(asset) ? runtimeState.histories.get(asset.code) : null;
    const rows = detail && Array.isArray(detail.history)
      ? detail.history.filter(row => numberOr(row.pe_percentile) !== null)
      : [];
    const canvas=document.querySelector("#pe-chart");if(!canvas)return;
    if (rows.length < 2) {
      if (runtimeState.source === "api") {
        return isDetailLoading(asset)
          ? drawLoadingChart(canvas, "正在加载")
          : drawUnavailableChart(canvas, "暂无可用的真实 PE 百分位历史数据");
      }
      mockDrawPeChart(asset);
      bindChartTooltip(canvas, [], () => [
        `数据：本地 Mock · ${asset.date || "—"}`,
        `PE：${num(numberOr(asset.pe))}`,
        `PE百分位：${num(numberOr(asset.pep))}%`,
        `估值状态：${asset.valuation || "—"}`
      ]);
      return;
    }
    const dpr=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;
    canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext("2d");c.scale(dpr,dpr);c.clearRect(0,0,w,h);
    const left=44,right=w-14,top=14,bottom=h-32,y=value=>top+(100-value)*(bottom-top)/100,x=i=>left+i*(right-left)/(rows.length-1);
    [[0,15,"#edf7f1"],[15,35,"#f3f8ee"],[35,70,"#f7f7f4"],[70,90,"#fbf4e8"],[90,100,"#faecec"]].forEach(([lo,hi,color])=>{c.fillStyle=color;c.fillRect(left,y(hi),right-left,y(lo)-y(hi));});
    c.strokeStyle="#596f9f";c.lineWidth=2;c.beginPath();rows.forEach((row,i)=>i?c.lineTo(x(i),y(Number(row.pe_percentile))):c.moveTo(x(i),y(Number(row.pe_percentile))));c.stroke();
    c.fillStyle="#687684";c.font="10px sans-serif";c.textAlign="left";c.fillText(rows[0].trade_date,left,h-8);c.textAlign="right";c.fillText(rows[rows.length-1].trade_date,right,h-8);c.textAlign="left";
    bindChartTooltip(canvas, rows, event => {
      const rect = canvas.getBoundingClientRect();
      const index = Math.max(0, Math.min(rows.length - 1, Math.round((event.clientX - rect.left - left) / Math.max(right - left, 1) * (rows.length - 1))));
      const row = rows[index];
      return [`日期：${row.trade_date}`, `PE：${num(numberOr(row.pe))}`, `PE百分位：${num(numberOr(row.pe_percentile))}%`, `估值状态：${row.valuation_status || "—"}`];
    });
  };

  function showTooltip({tooltipId, host, html, event}) {
    let tooltip = document.querySelector(`#${tooltipId}`);
    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.id = tooltipId;
      tooltip.className = "chart-tooltip";
      tooltip.setAttribute("role", "tooltip");
    }
    // A modal dialog is rendered in the browser's top layer. Tooltips attached
    // to document.body sit below it, so mount each tooltip in its owning dialog.
    if (tooltip.parentElement !== host) host.appendChild(tooltip);
    tooltip.innerHTML = html;
    tooltip.style.visibility = "hidden";
    tooltip.style.left = "0px";
    tooltip.style.top = "0px";
    const bounds = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(event.clientX + 14, window.innerWidth - bounds.width - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(event.clientY + 14, window.innerHeight - bounds.height - 8))}px`;
    tooltip.style.visibility = "visible";
  }

  function bindChartTooltip(canvas, rows, formatter) {
    const tooltipId = "dashboard-chart-tooltip";
    const hide = () => document.querySelector(`#${tooltipId}`)?.remove();
    canvas.onmouseleave = hide;
    canvas.onmousemove = event => {
      const lines = formatter(event);
      const html = lines.map((line, index) => {
        const [label, ...rest] = String(line).split("：");
        const value = rest.join("：") || "—";
        return index === 0
          ? `<strong>${escapeHtml(label)}：${escapeHtml(value)}</strong>`
          : `<div class="chart-tooltip-row"><span>${escapeHtml(label)}</span><span>${escapeHtml(value)}</span></div>`;
      }).join("");
      showTooltip({tooltipId, host: canvas.closest("dialog[open]") || canvas.closest("dialog") || document.body, html, event});
    };
  }

  // The browser's native `title` popup has an intentional hover delay and does
  // not wrap reliably. Replace it with the same immediate, readable tooltip
  // used by the market charts, while keeping the chart markup data-driven.
  function bindResearchTooltips() {
    const selector = ".research-year[title], .anchor-item[title], .quality-line-canvas[title]";
    const tooltipId = "dashboard-research-tooltip";
    const hide = () => document.querySelector(`#${tooltipId}`)?.remove();
    const show = (target, event) => {
      const raw = target.dataset.researchTooltip || target.getAttribute("title") || "";
      if (!raw) return;
      target.dataset.researchTooltip = raw;
      target.removeAttribute("title");
      const lines = raw.split("｜").filter(Boolean);
      const html = lines.map((line, index) => {
        const [label, ...rest] = line.split("：");
        const value = rest.join("：");
        if (!value) return `<strong>${escapeHtml(line)}</strong>`;
        return index === 0
          ? `<strong>${escapeHtml(label)}：${escapeHtml(value)}</strong>`
          : `<div class="chart-tooltip-row"><span>${escapeHtml(label)}</span><span>${escapeHtml(value)}</span></div>`;
      }).join("");
      showTooltip({tooltipId, host: target.closest("dialog[open]") || target.closest("dialog") || document.body, html, event});
    };
    document.addEventListener("pointerover", event => {
      const target = event.target.closest(selector);
      if (target) show(target, event);
    });
    document.addEventListener("pointermove", event => {
      const target = event.target.closest(".research-year[data-research-tooltip], .anchor-item[data-research-tooltip], .quality-line-canvas[data-research-tooltip]");
      if (target) show(target, event);
    });
    document.addEventListener("pointerout", event => {
      const target = event.target.closest(".research-year[data-research-tooltip], .anchor-item[data-research-tooltip], .quality-line-canvas[data-research-tooltip]");
      if (target && !target.contains(event.relatedTarget)) hide();
    });
  }

  function drawUnavailableChart(canvas, message) {
    document.querySelector("#dashboard-chart-tooltip")?.remove();
    const dpr = window.devicePixelRatio || 1, width = canvas.clientWidth, height = canvas.clientHeight;
    canvas.width = width * dpr; canvas.height = height * dpr;
    const context = canvas.getContext("2d"); context.scale(dpr, dpr); context.clearRect(0, 0, width, height);
    context.fillStyle = "#687684"; context.font = "13px sans-serif"; context.textAlign = "center";
    context.fillText(message, width / 2, height / 2); context.textAlign = "left";
    canvas.onmousemove = null; canvas.onmouseleave = null;
  }

  function drawLoadingChart(canvas, message) {
    document.querySelector("#dashboard-chart-tooltip")?.remove();
    const dpr = window.devicePixelRatio || 1, width = canvas.clientWidth, height = canvas.clientHeight;
    canvas.width = width * dpr; canvas.height = height * dpr;
    const context = canvas.getContext("2d"); context.scale(dpr, dpr); context.clearRect(0, 0, width, height);
    const x = width / 2 - 74, y = height / 2;
    context.strokeStyle = "#5470c6"; context.lineWidth = 2;
    context.beginPath(); context.arc(x, y - 3, 8, Math.PI * .15, Math.PI * 1.75); context.stroke();
    context.fillStyle = "#687684"; context.font = "13px sans-serif"; context.textAlign = "left";
    context.fillText(message, x + 18, y + 2); context.textAlign = "left";
    canvas.onmousemove = null; canvas.onmouseleave = null;
  }

  document.addEventListener("click", event => {
    const range = event.target.closest("[data-range]");
    if (!range || runtimeState.source !== "api") return;
    const asset = detailType === "index" ? indices[detailIndex] : stocks[detailIndex];
    loadAssetDetail(asset, Number(range.dataset.range)).then(redrawActivePriceChart).catch(error => console.warn("历史数据加载失败：", error));
  });

  document.addEventListener("trend-dashboard-range-change", event => {
    if (runtimeState.source !== "api") return;
    const {months, type, index} = event.detail || {};
    const asset = type === "index" ? indices[index] : stocks[index];
    if (!asset) return;
    loadAssetDetail(asset, months).then(redrawActivePriceChart).catch(error => console.warn("历史数据加载失败：", error));
  });

  document.addEventListener("click", async event => {
    if (!event.target.closest("#dashboard-logout")) return;
    await window.TrendDashboardAuth.signOut();
    setSourceStatus("mock", "已退出 · 本地 Mock");
  });

  async function loadOverview() {
    // Authentication may intentionally wait for the user at the login gate.
    // Keep the visible Mock state truthful instead of leaving the bootstrap
    // placeholder in the header while that promise is pending.
    setSourceStatus("mock", "本地 Mock · 登录后读取生产数据");
    runtimeState.token = await window.TrendDashboardAuth.ensureAuthenticated();
    setSourceStatus("mock", "正在读取 Edge API…");
    try {
      const payload = await apiFetch("/overview");
      applyOverview(payload);
      const issues = payload.completeness && (payload.completeness.asset_issues || payload.completeness.missing_asset_signals);
      const issueLabels = Array.isArray(issues) ? issues.map(item => item && item.symbol).filter(Boolean) : [];
      const suffix = issueLabels.length ? ` · ${issueLabels.join("、")} 数据延迟` : "";
      setSourceStatus("api", `Edge API · 版本 ${String(payload.version || "—").slice(0, 8)}${suffix}`, payload.generated_at);
      renderAll();
    } catch (error) {
      if (String(error.message).includes("unauthorized")) {
        window.TrendDashboardAuth.clearSession();
        window.TrendDashboardAuth.showGate("登录已失效，请重新登录。");
      }
      setSourceStatus("error", `API 不可用，已回退 Mock：${error.message}`);
      renderAll();
    }
  }

  async function boot() {
    const existingNames = new Set(indices.map(item => item.name));
    indices.push(...EXTRA_MOCK_INDICES.filter(item => !existingNames.has(item.name)));
    pairs.splice(0, pairs.length, ...MOCK_COMPASS);
    renderAll();
    if (!runtimeState.apiBase || !window.TrendDashboardAuth) {
      setSourceStatus("mock", "本地 Mock · 12 个指数 / 9 只股票");
      return;
    }
    await loadOverview();
  }

  window.addEventListener("trend-dashboard-authenticated", () => loadOverview());
  window.addEventListener("trend-dashboard-signed-out", () => {
    runtimeState.source = "mock";
    setSourceStatus("mock", "已退出 · 本地 Mock");
  });
  bindResearchTooltips();
  boot();
})();
