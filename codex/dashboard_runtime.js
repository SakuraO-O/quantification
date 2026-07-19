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
    detailRequests: new Map()
  };
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
    const currentNumber = (value, fallbackValue) => delayed ? null : numberOr(value, fallbackValue);
    const currentText = (value, fallbackValue, unavailable = "数据延迟") => delayed ? unavailable : value || fallbackValue || "数据不足";
    return {
      ...fallback,
      name: asset.name || fallback.name || asset.symbol,
      code: asset.symbol || fallback.code,
      date: asset.trade_date || fallback.date || "—",
      dataStatus: asset.data_status || "current",
      dataIssue: asset.data_issue || null,
      lastValidDate: asset.last_valid_trade_date || null,
      close: currentNumber(asset.close, fallback.close),
      day: delayed ? null : returnPercent(asset.daily_return) ?? fallback.day ?? 0,
      pe: currentNumber(asset.pe, fallback.pe),
      pep: delayed ? null : numberOr(asset.pe_percentile, fallback.pep),
      value: currentText(asset.valuation_status, fallback.value),
      advice: currentText(asset.investment_advice, fallback.advice, "暂不判断"),
      status: currentText(asset.overall_status, fallback.status),
      short: currentText(asset.short_trend, fallback.short),
      mid: currentText(asset.mid_trend, fallback.mid),
      long: currentText(asset.long_trend, fallback.long),
      ytd: delayed ? null : returnPercent(asset.return_ytd) ?? fallback.ytd ?? 0,
      w: delayed ? null : returnPercent(asset.return_1w) ?? fallback.w ?? 0,
      m: delayed ? null : returnPercent(asset.return_1m) ?? fallback.m ?? 0,
      y: delayed ? null : returnPercent(asset.return_1y) ?? fallback.y ?? 0,
      y3: delayed ? null : returnPercent(asset.return_3y) ?? fallback.y3 ?? 0,
      ma120: currentNumber(asset.ma120, fallback.ma120),
      ma200: currentNumber(asset.ma200, fallback.ma200),
      s120: delayed ? null : returnPercent(asset.ma120_slope_20d) ?? fallback.s120,
      s200: delayed ? null : returnPercent(asset.ma200_slope_40d) ?? fallback.s200,
      div: numberOr(asset.last_year_dividend, fallback.div ?? 0),
      yield: delayed ? blankDecimal : numberOr(asset.dividend_yield, fallback.yield ?? 0),
      dividendSafety: assessment.dividend_safety_status || asset.dividend_safety_status || fallback.dividendSafety || "数据不足",
      fund: assessment.fundamental_status || asset.fundamental_status || fallback.fund || "数据不足",
      period: assessment.report_period || fallback.period || "数据不足",
      announce: asset.latest_announcement_date || fallback.announce || "数据不足",
      template: templateAlias(asset.industry_template) || fallback.template,
      sourceTemplate: asset.industry_template,
      anchor: fallback.anchor || templateAnchor(asset.industry_template),
      change: assessment.main_risk || fallback.change || "尚无稳定的结构化基本面结论。",
      assessment
    };
  }

  function applyOverview(payload) {
    const assets = Array.isArray(payload.assets) ? payload.assets : [];
    const apiIndices = assets.filter(item => item.asset_type === "指数").map(item => mapAsset(item, "index"));
    const apiStocks = assets.filter(item => item.asset_type === "股票").map(item => mapAsset(item, "stock"));
    if (apiIndices.length !== 12 || apiStocks.length !== 9) {
      throw new Error(`资产清单不完整：指数 ${apiIndices.length}/12，股票 ${apiStocks.length}/9`);
    }
    indices.splice(0, indices.length, ...apiIndices);
    stocks.splice(0, stocks.length, ...apiStocks);

    const rows = payload.allocation && Array.isArray(payload.allocation.rows) ? payload.allocation.rows : [];
    if (rows.length === categories.length) {
      const colors = new Map(categories.map(item => [item.name, item.color]));
      categories.splice(0, categories.length, ...rows.map(row => ({
        name: row.category,
        color: colors.get(row.category),
        target: numberOr(row.target_ratio, 0),
        actual: numberOr(row.actual_amount, 0)
      })));
    }

    const compass = Array.isArray(payload.style_compass) ? payload.style_compass : [];
    if (compass.length) {
      pairs.splice(0, pairs.length, ...compass.map(item => ({
        left: item.left && item.left.name,
        right: item.right && item.right.name,
        d: [20, 60, 120].map(window => returnPercent(item[`return_${window}d_diff`]) ?? 0),
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

  const originalRenderFocus = renderFocus;
  renderFocus = function renderDynamicFocus() {
    if (!indices.length || !stocks.length) return originalRenderFocus();
    const indexWithPe = indices.filter(item => item.pep !== null && item.pep !== undefined);
    const highestPe = [...indexWithPe].sort((a,b) => b.pep - a.pep)[0];
    const lowestPe = [...indexWithPe].sort((a,b) => a.pep - b.pep)[0];
    const highestYield = stocks.filter(item => Number.isFinite(item.yield)).sort((a,b) => b.yield - a.yield)[0];
    const dividendWatch = stocks.find(item => item.dividendSafety === "承压") || stocks.find(item => item.dividendSafety === "关注") || stocks[0];
    const candidates = [
      highestPe && {type:"index", asset:highestPe, label:"PE百分位", value:`${highestPe.pep.toFixed(2)}%`, cls:"risk"},
      lowestPe && {type:"index", asset:lowestPe, label:"PE百分位", value:`${lowestPe.pep.toFixed(2)}%`, cls:"good"},
      highestYield && {type:"stock", asset:highestYield, label:"股息率", value:`${highestYield.yield.toFixed(2)}%`, cls:"good"},
      dividendWatch && {type:"stock", asset:dividendWatch, label:"分红保障", value:dividendWatch.dividendSafety, cls:dividendWatch.dividendSafety === "稳健" ? "good" : "risk"}
    ].filter(Boolean);
    document.querySelector("#focus-grid").innerHTML = candidates.map(item => {
      const list = item.type === "index" ? indices : stocks;
      const i = list.indexOf(item.asset);
      return `<button class="focus-card ${item.cls}" data-focus-type="${item.type}" data-focus-index="${i}"><div class="focus-top"><div><div class="focus-name">${item.asset.name}</div><div class="focus-code">${displayCode(item.asset.code)} · ${item.type === "index" ? "指数" : "股票"}</div></div><div class="focus-price"><b>${num(item.asset.close)}</b><div class="${item.asset.day > 0 ? "pos" : "neg"}">${pct(item.asset.day)}</div></div></div><div class="focus-metrics"><div class="focus-reason"><small>${item.label}</small><b>${item.value}</b></div><div><small>综合状态</small><b>${item.asset.status}</b></div></div></button>`;
    }).join("");
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
    profile.summary = assessment.fundamental_status || profile.summary;
    const evidence = Array.isArray(assessment.evidence) ? assessment.evidence : [];
    profile.evidence = evidence.length
      ? evidence.map(item => typeof item === "string" ? item : item.summary || item.metric_code || JSON.stringify(item)).slice(0, 5)
      : profile.evidence;
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
    let html = originalStockDetail(asset);
    if (runtimeState.source === "api") {
      html = html
        .replace("最近8个季度 · 原型示例", "最近8个季度 · Mock 占位（真实适配器待接入）")
        .replace("数据来源：年度报告、季度报告、行业经营披露；当前所有数值均为原型示例。", "数据来源：结构化来源元数据详见“基本面—年度”；不保存原始文件或响应全文。");
    }
    return html;
  };

  const originalIndexDetail = indexDetail;
  indexDetail = function indexDetailFromApi(asset) {
    let html = originalIndexDetail(asset);
    if (runtimeState.histories.has(asset.code)) {
      html = html.replace("原型曲线为交互示意，实际版本需展示真实历史数据、数据来源和抓取时间。", "当前曲线来自 Edge API 返回的真实结构化历史数据。");
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
  async function loadAssetDetail(asset, months) {
    if (runtimeState.source !== "api") return;
    const key = `${asset.code}:${rangeKey(months)}`;
    if (runtimeState.detailRequests.has(key)) return runtimeState.detailRequests.get(key);
    const request = apiFetch(`/asset/${encodeURIComponent(asset.code)}?range=${rangeKey(months)}`)
      .then(detail => {
        runtimeState.histories.set(asset.code, detail);
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
    const detail = runtimeState.histories.get(asset.code);
    const historyRows = detail && Array.isArray(detail.history) ? detail.history : [];
    if (historyRows.length < 2) return mockDrawPriceChart(asset, type);
    const canvas = document.querySelector("#price-chart");
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const c = canvas.getContext("2d"); c.scale(dpr, dpr); c.clearRect(0, 0, w, h);
    const rows = historyRows.filter(row => numberOr(row.close) !== null);
    const left = 60, right = w - 14, top = 18, bottom = h - 36;
    const keys = {ma20:"#75a7d8",ma60:"#8fc4b5",ma120:"#b09bc9",ma200:"#d7a68f"};
    const values = rows.flatMap(row => [numberOr(row.close), ...[...selectedMAs].map(key => numberOr(row[key]))]).filter(value => value !== null);
    const min = Math.min(...values) * .98, max = Math.max(...values) * 1.02;
    const x = i => left + i * (right-left) / Math.max(rows.length-1, 1);
    const y = value => top + (max-value) * (bottom-top) / Math.max(max-min, 1e-9);
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
  };

  const mockDrawPeChart = drawPeChart;
  drawPeChart = function drawApiPeChart(asset) {
    const detail = runtimeState.histories.get(asset.code);
    const rows = detail && Array.isArray(detail.history)
      ? detail.history.filter(row => numberOr(row.pe_percentile) !== null)
      : [];
    if (rows.length < 2) return mockDrawPeChart(asset);
    const canvas=document.querySelector("#pe-chart");if(!canvas)return;
    const dpr=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;
    canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext("2d");c.scale(dpr,dpr);c.clearRect(0,0,w,h);
    const left=44,right=w-14,top=14,bottom=h-32,y=value=>top+(100-value)*(bottom-top)/100,x=i=>left+i*(right-left)/(rows.length-1);
    [[0,15,"#edf7f1"],[15,35,"#f3f8ee"],[35,70,"#f7f7f4"],[70,90,"#fbf4e8"],[90,100,"#faecec"]].forEach(([lo,hi,color])=>{c.fillStyle=color;c.fillRect(left,y(hi),right-left,y(lo)-y(hi));});
    c.strokeStyle="#596f9f";c.lineWidth=2;c.beginPath();rows.forEach((row,i)=>i?c.lineTo(x(i),y(Number(row.pe_percentile))):c.moveTo(x(i),y(Number(row.pe_percentile))));c.stroke();
    c.fillStyle="#687684";c.font="10px sans-serif";c.textAlign="left";c.fillText(rows[0].trade_date,left,h-8);c.textAlign="right";c.fillText(rows[rows.length-1].trade_date,right,h-8);c.textAlign="left";
  };

  document.addEventListener("click", event => {
    const range = event.target.closest("[data-range]");
    if (!range || runtimeState.source !== "api") return;
    const asset = detailType === "index" ? indices[detailIndex] : stocks[detailIndex];
    loadAssetDetail(asset, Number(range.dataset.range)).then(redrawActivePriceChart).catch(error => console.warn("历史数据加载失败：", error));
  });

  document.addEventListener("click", async event => {
    if (!event.target.closest("#dashboard-logout")) return;
    await window.TrendDashboardAuth.signOut();
    setSourceStatus("mock", "已退出 · 本地 Mock");
  });

  async function loadOverview() {
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
  boot();
})();
