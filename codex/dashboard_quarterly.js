/* Presentation of Python-published quarterly figures; no financial rules here. */
window.TrendQuarterly = (() => {
  const keys = ["net_profit", "deducted_net_profit", "revenue", "operating_cashflow", "roe", "asset_liability_ratio"];
  const ratio = key => key === "roe" || key === "asset_liability_ratio";
  const escape = value => String(value ?? "—").replace(/[&<>"']/g, char =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const number = (value, key) => value == null ? "数据不足" :
    `${(Number(value) / (ratio(key) ? 1 : 1e8)).toFixed(2)}${ratio(key) ? "%" : "亿元"}`;
  const tableNumber = (value, key) => (Number(value) / (ratio(key) ? 1 : 1e8)).toFixed(2) + (ratio(key) ? "%" : "");
  const percent = value => value?.value == null ? escape(value?.status || "数据不足") :
    `${value.value > 0 ? "+" : ""}${Number(value.value).toFixed(2)}%`;
  const negative = value => value < 0 ? "quarter-negative" : "";
  const source = item => item
    ? `${item.report_period || "报告期待核对"} · ${item.source || "来源待核对"} · ${item.announcement_date || "公告日期待核对"}`
    : "待核对";
  const tooltip = (year, quarter, data, cell, key) => escape([
    `${year}年${quarter}季度 · ${data.label}`, number(cell.value, key),
    `口径：${({single_quarter:"单季度",year_to_date:"年初至今累计",quarter_end:"季度末"})[cell.basis] || "—"}`,
    `本期依据：${source(cell.source)}`,
    ...(cell.minus_source ? [`减项依据：${source(cell.minus_source)}`] : [])
  ].join("|"));

  function chart(data, years, key) {
    const points = data.years.flatMap((column, i) => column.quarters.map((cell, j) =>
      ({year:years[i], quarter:j + 1, cell, value:cell.value})));
    const values = points.map(item => item.value).filter(value => value != null);
    if (!values.length) return '<div class="data-missing">该指标暂无已核验季度数据。</div>';
    if (ratio(key)) {
      const min = Math.min(...values), span = Math.max(...values) - min || 1;
      const xy = (item, index) => ({x:20 + index * 28, y:190 - (item.value - min) / span * 160});
      const groups = []; let group = [];
      points.forEach((item, index) => {
        if (item.value == null) { if (group.length) groups.push(group); group = []; }
        else group.push(xy(item, index));
      });
      if (group.length) groups.push(group);
      const paths = groups.map(items => {
        const [first, ...rest] = items;
        const d = `M ${first.x} ${first.y}` + rest.map((point, i) => {
          const prior = items[i], dx = (point.x - prior.x) / 3;
          return ` C ${prior.x + dx} ${prior.y} ${point.x - dx} ${point.y} ${point.x} ${point.y}`;
        }).join("");
        return `<path d="${d}" fill="none" stroke="#5470c6" stroke-width="2.5"/>`;
      }).join("");
      const dots = points.map((item, index) => item.value == null ? "" :
        `<circle cx="${xy(item,index).x}" cy="${xy(item,index).y}" r="5" fill="#5470c6" data-quarter-negative="${item.value < 0}" data-quarter-tip="${tooltip(item.year,item.quarter,data,item.cell,key)}"/>`).join("");
      const labels = years.map((year, i) => `<text x="${62 + i * 112}" y="219" text-anchor="middle">${year}</text>`).join("");
      return `<div class="quarter-chart-scroll"><svg class="quarter-line-chart" viewBox="0 0 1140 230" role="img" aria-label="${escape(data.label)}近十年季度曲线">${paths}${dots}${labels}</svg></div>`;
    }
    const maximum = Math.max(...values.map(value => Math.abs(value)), 1);
    const columns = years.map((year, i) => `<div class="quarter-year"><div class="quarter-bars">${data.years[i].quarters.map((cell, j) => {
      if (cell.value == null) return '<span class="quarter-absent"></span>';
      const height = Math.max(2, Math.abs(cell.value) / maximum * 94);
      return `<i class="quarter-bar ${negative(cell.value)}" style="left:${8 + j * 23}px;height:${height}px;${cell.value < 0 ? "top:96px" : "bottom:99px"}" data-quarter-negative="${cell.value < 0}" data-quarter-tip="${tooltip(year,j+1,data,cell,key)}"></i>`;
    }).join("")}</div><span>${year}</span></div>`).join("");
    return `<div class="quarter-chart-scroll"><div class="quarter-bar-chart"><div class="quarter-zero-line"></div>${columns}</div></div>`;
  }

  function render(key, quarterly) {
    const host = document.querySelector("#quarter-content");
    activeTarget = null;
    document.querySelector("#quarter-tooltip")?.remove();
    const data = quarterly?.metrics?.[key], years = quarterly?.years;
    if (!host || !data) return;
    const rows = [1,2,3,4].map(q => [`Q${q}`, ...data.years.map(year => year.quarters[q-1])]);
    if (ratio(key)) rows.push([key === "roe" ? "年度ROE" : "年末资产负债率",
      ...data.years.map(year => year.year_quarter === 4 ? {value:year.year_value} : {value:null,status:"未披露"})]);
    else {
      rows.push(["全年／已披露累计", ...data.years.map(year => ({value:year.year_value,status:year.year_status}))]);
      rows.push(["同期累计同比", ...data.years.map(year => year.comparison)]);
    }
    rows.push(["每股分红（元/股）", ...years.map(year => quarterly.dividends[String(year)])]);
    rows.push(["股利支付率", ...years.map(year => quarterly.dividends[String(year)])]);
    const body = rows.map(([label,...cells]) => `<tr class="${["全年／已披露累计", "年度ROE", "年末资产负债率"].includes(label) ? "quarter-annual-total" : ""}"><th>${escape(label)}</th>${cells.map(cell => {
      let value;
      if (label === "每股分红（元/股）") value = cell?.per_share == null
        ? escape(cell?.status || "数据不足")
        : `${Number(cell.per_share).toFixed(4)}${cell.unimplemented ? "（含已批准未实施）" : ""}${cell.status !== "已核验" ? `（已确认部分；${escape(cell.status)}）` : ""}`;
      else if (label === "股利支付率") value = cell?.payout_ratio == null
        ? escape(cell?.status || "数据不足") : `${Number(cell.payout_ratio).toFixed(2)}%`;
      else if (label === "同期累计同比") value = percent(cell);
      else value = cell?.value == null ? (cell?.status === "未披露" ? "-" : escape(cell?.reason || cell?.status || "数据不足")) : tableNumber(cell.value, key);
      const tone = negative(cell?.value);
      return `<td class="${tone} ${label.startsWith("每股分红") && cell?.per_share === 0 ? "quarter-zero" : ""}">${value}</td>`;
    }).join("")}</tr>`).join("");
    host.innerHTML = chart(data, years, key) +
      `<div class="quarter-table-scroll"><table class="quarter-matrix"><thead><tr><th>${escape(data.label)}（${ratio(key) ? "%" : "亿元"}）</th>${years.map(year => `<th>${year}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function pane(quarterly) {
    if (!quarterly || quarterly.status !== "已计算") return `<div class="data-missing">${escape(quarterly?.reason || "季度数据尚未发布或正式报告尚未核验")}</div>`;
    const top = quarterly.top;
    const summary = (key, label) => {
      const item = top.summary[key];
      return `<div class="metric-card"><small>年初至今${label}</small><b class="${negative(item?.value)}">${number(item?.value,key)}</b><small class="${negative(item?.comparison?.value)}">同期同比 ${percent(item?.comparison)}</small></div>`;
    };
    const perShare = value => value == null ? "—" : `${Number(value).toFixed(4)} 元/股`;
    const dividend = top.dividend || {}, payout = top.payout || {};
    return `<div class="quarter-summary">
      <div class="metric-card"><small>最新报告期</small><b>${escape(top.report_period)}</b><small>公告日期：${escape(top.announcement_date || "待核对")}</small></div>
      ${summary("net_profit","归母净利润")}${summary("deducted_net_profit","扣非归母净利润")}${summary("revenue","营业总收入")}
      <div class="metric-card"><small>${top.dividend_year}年每股分红</small><b>已实施：${perShare(dividend.implemented_per_share)}</b><small>已批准未实施：${perShare(dividend.approved_per_share)} · ${escape(dividend.status)}</small></div>
      <div class="metric-card"><small>上一年度股利支付率 · ${escape(top.payout_year || "—")}报告年度</small><b>${payout.payout_ratio == null ? escape(payout.status || "数据不足") : Number(payout.payout_ratio).toFixed(2) + "%"}</b></div>
    </div><div class="chart-box"><div class="chart-title"><h4>近10年季度经营</h4><small>仅展示已核验财报${quarterly.year_range_issue ? ` · ${escape(quarterly.year_range_issue)}` : ""}</small></div>
      <div class="quarter-control">${keys.map((key,i) => `<button class="chart-chip ${i ? "" : "active"}" data-quarter-metric="${key}" aria-pressed="${!i}">${escape(quarterly.metrics[key]?.label || key)}</button>`).join("")}</div>
      <div id="quarter-content"></div></div>`;
  }

  let activeTarget = null;
  let pointerPosition = null;
  document.addEventListener("pointermove", event => {
    pointerPosition = {x:event.clientX, y:event.clientY};
    const target = event.target.closest?.("[data-quarter-tip]");
    const tip = document.querySelector("#quarter-tooltip");
    if (!target) {
      activeTarget = null;
      if (tip) tip.hidden = true;
      return;
    }
    const host = target.closest("dialog") || document.body;
    const visibleTip = tip || Object.assign(document.createElement("div"), {id:"quarter-tooltip", className:"chart-tooltip"});
    if (visibleTip.parentElement !== host) host.appendChild(visibleTip);
    if (target !== activeTarget) {
      activeTarget = target;
      visibleTip.replaceChildren(...target.dataset.quarterTip.split("|").map((line, index) => {
        const row = document.createElement("div");
        row.textContent = line;
        if (index === 1 && target.dataset.quarterNegative === "true") row.className = "quarter-tip-negative";
        return row;
      }));
    }
    visibleTip.style.visibility = "hidden";
    visibleTip.hidden = false;
    visibleTip.style.left = `${Math.max(8, event.clientX + 334 > innerWidth ? event.clientX - 334 : event.clientX + 14)}px`;
    const bounds = visibleTip.getBoundingClientRect();
    visibleTip.style.left = `${Math.max(8, Math.min(event.clientX + 14, innerWidth - bounds.width - 8))}px`;
    visibleTip.style.top = `${Math.max(8, Math.min(event.clientY + 14, innerHeight - bounds.height - 8))}px`;
    visibleTip.style.visibility = "visible";
  });
  const hideTooltip = () => {
    const tip = document.querySelector("#quarter-tooltip");
    activeTarget = null;
    if (tip) tip.hidden = true;
  };
  document.addEventListener("mouseleave", hideTooltip);
  document.addEventListener("scroll", event => {
    const hovered = pointerPosition && document.elementFromPoint(pointerPosition.x, pointerPosition.y)?.closest("[data-quarter-tip]");
    if (event.target.matches?.(".quarter-chart-scroll, .quarter-table-scroll") || hovered !== activeTarget) hideTooltip();
  }, true);
  document.addEventListener("click", hideTooltip);
  document.addEventListener("close", hideTooltip, true);
  return {pane, render};
})();
