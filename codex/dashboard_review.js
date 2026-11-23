(function () {
  'use strict';
  const config = window.TREND_DASHBOARD_CONFIG || {};
  const base = config.reviewApiBase || String(config.apiBase || '').replace(/dashboard-api\/?$/, 'quarterly-review');
  if (!base || !window.TrendDashboardAuth) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {pending:'待核实',changed:'更新待确认',diff:'存在差异',basis:'口径待确认',failed:'校验失败',auto:'自动通过',manual:'人工确认',legacy:'历史已核验'};
  const unit = value => ({CNY:'元',percent:'%', 'CNY/share':'元/股'}[value] || value || '');
  const fmt = value => value == null ? '—' : Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN',{maximumFractionDigits:6}) : esc(value);
  const periodLabel = value => {const match=String(value||'').match(/^(\d{4})-(03-31|06-30|09-30|12-31)$/);return match?`${match[1]}Q${({'03-31':1,'06-30':2,'09-30':3,'12-31':4})[match[2]]}`:value||'报告期待确认';};
  const displayNumber=(value,basis)=>{if(value==null)return '—';const scaled=basis==='CNY'?Number(value)/100000000:Number(value);return scaled!==0&&Math.abs(scaled)<0.000001?(scaled<0?'-':'')+'&lt;0.000001':fmt(scaled);};
  const displayUnit=basis=>basis==='CNY'?'亿元':unit(basis);
  const ratio=value=>value==null?'—':Number(value).toFixed(2)+'%';
  const time = value => {
    if (!value) return '—';
    const date = new Date(value); if (!Number.isFinite(+date)) return esc(value);
    return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(date);
  };
  const tag = item => `<span class="review-tag ${esc(item.status)}">${esc(labels[item.status] || item.status)}</span>`;
  const link = (url, text) => /^https?:\/\//i.test(url || '') ? `<a class="review-link" target="_blank" rel="noopener noreferrer" href="${esc(url)}">${esc(text)}</a>` : esc(text || '—');
  const state = {summary:null,tab:'pending',page:0,items:[],total:0,years:[],selected:new Set(),detail:null,sequence:0,detailSequence:0,loggedIn:false,busy:false,confirm:null};
  const entry = document.createElement('button'); entry.id = 'review-entry'; entry.type = 'button'; entry.textContent = '季度核实'; entry.hidden = true;
  document.querySelector('.topmeta')?.append(entry);
  const work = document.createElement('dialog'); work.id = 'review-workbench'; work.className = 'review-dialog';
  work.setAttribute('aria-labelledby','review-title');
  work.innerHTML = `<div class="review-head"><div><h2 id="review-title">季度数据核实</h2><p>股票季度财报与分红 · 正常数据自动通过，仅处理异常</p></div><div class="review-actions"><button class="btn" data-review="refresh">刷新</button><button class="btn" data-review="recheck-all">校验全部待核实</button><button class="btn primary" data-review="publish">更新看板</button><button class="btn" data-review="close-work" aria-label="关闭核实工作台">关闭</button></div></div>
    <div class="review-banner" id="review-summary"></div><div class="review-tabs" role="tablist" aria-label="核实分类">${[['pending','需处理'],['auto','自动通过'],['manual','人工记录'],['annual','年度查全']].map(([id,name])=>`<button role="tab" data-review-tab="${id}" aria-selected="${id==='pending'}">${name} <span id="review-count-${id}"></span></button>`).join('')}</div>
    <form class="review-controls" id="review-filters"><label>股票<select name="security_id" aria-label="筛选股票"><option value="">全部股票</option></select></label><label>报告期<select name="report_period" aria-label="筛选报告期"><option value="">全部报告期</option></select></label><label id="review-status-field">核实状态<select name="status" aria-label="筛选核实状态"><option value="">全部状态</option>${Object.entries(labels).filter(([k])=>!['auto','manual','legacy'].includes(k)).map(([key,value])=>`<option value="${key}">${value}</option>`).join('')}</select></label><label id="review-search-field">搜索指标<input name="search" placeholder="例如：归母净利润" maxlength="100"></label><button class="btn" type="submit">查询</button></form>
    <div id="review-list" class="review-scroll" aria-live="polite"></div><div id="review-message" class="review-banner" role="status"></div><div class="review-foot"><div id="review-pagination"></div><div class="review-actions"><button class="btn" data-review="previous">上一页</button><button class="btn" data-review="next">下一页</button><button class="btn primary" data-review="batch">确认已查证记录（0）</button></div></div>`;
  const drawer = document.createElement('dialog'); drawer.id = 'review-detail'; drawer.className = 'review-dialog'; drawer.setAttribute('aria-labelledby','review-detail-title');
  const confirm = document.createElement('dialog'); confirm.id = 'review-confirm'; confirm.className = 'review-dialog'; confirm.setAttribute('aria-labelledby','review-confirm-title');
  document.body.append(work,drawer,confirm);
  const filters = work.querySelector('#review-filters');
  const message = (text, error=false) => {const node=work.querySelector('#review-message');node.textContent=text;node.className=`review-banner ${error?'review-error':'review-muted'}`;};
  const friendly = error => {
    const text=String(error.message||error);
    const messages={review_version_changed:'数据版本已变化，请刷新后重新核对。',review_item_not_current:'该记录已有新版本，请刷新列表。',review_task_active:'该记录正在排队或校验，请等待结果。',review_metadata_incomplete:'请先补齐必要口径信息。',review_year_not_ready:'年度尚未披露完整，或仍有未处理分红。',review_plan_link_required:'已有同方案记录，请选择要关联的原方案。',review_target_exists:'目标报告期已有当前记录，不能重复创建。',review_evidence_required:'请选择具有链接和存档指纹的来源依据。',review_formal_evidence_required:'年度查全需要正式公告及存档指纹。',review_year_changed:'年度范围已变化，请刷新重新查全。',review_reference_not_comparable:'参考值的证券、报告期、单位或口径不一致，请先纠正口径。',review_evidence_period_mismatch:'来源依据的报告期与本次处理不一致，请重新选择。',review_invalid_value:'请输入有效十进制数，不接受空值或无穷值。',review_invalid_precision_or_sign:'金额最多14位小数，比率和每股金额最多6位小数；分红金额不可为负。',review_invalid_unit:'单位与指标定义不一致，请核对来源。',review_invalid_basis:'报告期或统计口径不一致，请核对来源。',review_currency_unsupported:'本期仅支持人民币口径。',review_year_zero_evidence:'请确认全年无现金分红依据，已有正分红方案时不可选零。',review_year_no_dividend:'尚无已确认的正分红方案，请重新查全。',review_official_url_required:'请使用巨潮、上交所或深交所的正式HTTPS链接。',review_evidence_too_large:'该公告超过12MB，请选择同一报告期的较小正式文件。',review_evidence_fetch_failed:'未能获取公告文件，请检查链接后重试。',review_note_required:'请填写处理说明。',review_evidence_not_reviewed:'请查阅来源依据并勾选确认。'};
    const key=Object.keys(messages).find(k=>text.includes(k));
    return messages[key]||(text.includes('unauthorized')?'登录已失效，请重新登录。':'操作未完成，请刷新后重试；已提交的数据以最新状态为准。');
  };
  async function api(path, body) {
    const token=await window.TrendDashboardAuth.getAccessToken(); if (!token || !state.loggedIn) throw new Error('unauthorized');
    const response=await fetch(base+path,{method:body===undefined?'GET':'POST',headers:{Authorization:`Bearer ${token}`,apikey:window.TrendDashboardAuth.publishableKey(),'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const result=await response.json(); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`); return result;
  }
  function summaryView() {
    const summary=state.summary;
    if (!summary) {entry.textContent='季度核实 · 状态未知'; entry.classList.remove('has-pending');work.querySelector('#review-summary').textContent='核实状态读取失败，请刷新重试。';work.querySelector('[data-review=publish]').disabled=true;return;}
    const count=Number(summary.pending_facts||0)+Number(summary.pending_years||0);
    entry.textContent=count ? `待核实 ${count}` : summary.pending_publish ? `季度核实 · 待更新 ${summary.pending_publish}` : '季度核实';
    entry.classList.toggle('has-pending',count>0);
    for (const [tab,n] of Object.entries({pending:summary.pending_facts,auto:summary.auto,manual:summary.manual,annual:summary.pending_years})) work.querySelector(`#review-count-${tab}`).textContent=n??'—';
    const worker=summary.worker; const online=worker?.state==='online' && worker?.seen_at && Date.now()-new Date(worker.seen_at).getTime()<6*60000;
    const pub=summary.latest_publish_job;
    work.querySelector('#review-summary').innerHTML=`<span>财报及分红 ${esc(summary.pending_facts)} 项待处理 · 年度查全 ${esc(summary.pending_years)} 项 · 待更新 ${esc(summary.pending_publish)} 项</span><span>本地校验：${online?'在线':'离线或未连接'}${worker?.seen_at?` · 最后连接 ${time(worker.seen_at)}`:''}${pub?`<br>云端更新：${esc(({queued:'排队中',running:'执行中',completed:'成功',failed:'失败，可重试',expired:'已过期'})[pub.state]||pub.state)}${pub.error?` · ${esc(pub.error)}`:''}`:''}</span>`;
    work.querySelector('[data-review="publish"]').disabled=state.busy || !summary.pending_publish || ['queued','running'].includes(pub?.state);
  }
  async function refreshSummary() {try{const old=state.summary;state.summary=await api('/summary');summaryView();if(old&&(old.revision!==state.summary.revision||old.published_revision!==state.summary.published_revision))await window.TrendDashboardRuntime?.refresh();}catch(error){state.summary=null;summaryView();throw error;}}
  async function refreshFilters() {
    const result=await api('/filters'); const stock=filters.elements.security_id,period=filters.elements.report_period;
    const oldStock=stock.value,oldPeriod=period.value;
    stock.innerHTML='<option value="">全部股票</option>'+result.stocks.map(s=>`<option value="${esc(s.security_id)}">${esc(s.name)} · ${esc(s.symbol)}</option>`).join('');
    period.innerHTML='<option value="">全部报告期</option>'+result.periods.map(p=>`<option value="${esc(p)}">${esc(periodLabel(p))}</option>`).join('');
    stock.value=oldStock;period.value=oldPeriod;
  }
  function setBusy(value) {if(state.busy===value)return;state.busy=value;summaryView();confirm.querySelectorAll('button').forEach(b=>b.disabled=value);drawer.querySelectorAll('button[data-action]').forEach(b=>{if(value){b.dataset.wasDisabled=String(b.disabled);b.disabled=true;}else if(b.dataset.wasDisabled!==undefined){b.disabled=b.dataset.wasDisabled==='true';delete b.dataset.wasDisabled;}});updateSelection();}
  function updateSelection() {
    const all=work.querySelector('#review-select-all');
    if(all){const eligible=state.items.filter(i=>i.review_data?.ready&&i.verification_status!=='verified');const count=eligible.filter(i=>state.selected.has(i.id)).length;all.checked=eligible.length>0&&count===eligible.length;all.indeterminate=count>0&&count<eligible.length;}

    work.querySelector('[data-review="batch"]').textContent=`确认已查证记录（${state.selected.size}）`;
    work.querySelector('[data-review="batch"]').disabled=state.busy || !state.selected.size;
  }
  function renderList() {
    const annual=state.tab==='annual';
    work.querySelector('#review-status-field').hidden=state.tab!=='pending';work.querySelector('#review-search-field').hidden=annual;
    work.querySelector('[data-review="batch"]').hidden=state.tab!=='pending';
    const rows=state.items;
    if (!rows.length) work.querySelector('#review-list').innerHTML=`<div class="review-empty">${annual?'当前筛选下没有年度记录。':'当前筛选下没有记录。'}${state.tab==='pending'?' 已自动通过的记录可在“自动通过”中查看。':''}</div>`;
    else if (annual) work.querySelector('#review-list').innerHTML=`<table class="review-table"><thead><tr><th>状态</th><th>股票</th><th>报告年度</th><th>年度查全</th><th>未处理分红</th><th>最近确认</th><th>操作</th></tr></thead><tbody>${rows.map((item,index)=>`<tr><td>${item.ready?'可查全':'等待披露'}</td><td><strong>${esc(item.name)}</strong><small>${esc(item.symbol)}</small></td><td>${esc(item.fiscal_year)}年</td><td>${item.status==='complete'?'全年已确认':item.ready?'年度待确认':'尚不计入提醒'}</td><td>${esc(item.unresolved)}</td><td>${time(item.reviewed_at)}</td><td><button class="review-link" data-annual-index="${index}">${item.ready&&item.status!=='complete'?'查全处理':'查看'}</button></td></tr>`).join('')}</tbody></table>`;
    else work.querySelector('#review-list').innerHTML=`<table class="review-table"><thead><tr><th><input id="review-select-all" type="checkbox" aria-label="勾选本页已查证记录"></th><th>股票</th><th>报告期</th><th>指标 / 口径</th><th class="review-number">候选值</th><th class="review-number">参考值</th><th class="review-number">差异 / 比例</th><th>核实状态</th><th>最近校验</th><th>操作</th></tr></thead><tbody>${rows.map((item,index)=>{
      const data=item.review_data||{},ref=data.reference||{};
      return `<tr><td><input data-review-select="${esc(item.id)}" type="checkbox" aria-label="选择${esc(item.name)}${esc(item.metric_name)}" ${data.ready&&item.verification_status!=='verified'?'':'disabled'} ${state.selected.has(item.id)?'checked':''}></td><td><strong>${esc(item.name)}</strong><small>${esc(item.symbol)}</small></td><td>${esc(periodLabel(item.report_period))}</td><td><strong>${esc(item.metric_name)}</strong><small>${esc((item.metric_code==='asset_liability_ratio'?'期末':({year_to_date:'年初至今累计',annual:'全年累计',event:'税前人民币 / 单笔方案'})[item.period_type])||item.period_type)}</small></td><td class="review-number"><strong>${displayNumber(item.value,item.unit)}</strong> ${displayUnit(item.unit)}<small>${esc(item.source)}</small></td><td class="review-number"><strong>${displayNumber(ref.value,ref.unit)}</strong> ${ref.value==null?'':displayUnit(ref.unit)}<small>${esc(ref.source||'未取得参考')}</small></td><td class="review-number">${Number(ref.difference)>0?'+':''}${displayNumber(ref.difference,item.unit)}${ref.difference==null?'':item.unit==='percent'?' 个百分点':' '+displayUnit(item.unit)}<small>${ratio(ref.difference_ratio)}</small></td><td>${tag(item)}<small>${esc(data.reason||'')}${data.ready?' · 已查证待批量确认':''}</small></td><td>${time(data.checked_at)}<small>事实 v${esc(item.version)}</small></td><td><button class="review-link" data-detail-index="${index}">${item.verification_status==='verified'?'查看记录':'查看处理'}</button></td></tr>`;
    }).join('')}</tbody></table>`;
    work.querySelector('#review-pagination').textContent=`当前 ${state.total} 项 · 第 ${state.page+1} 页 · 每页50项${state.tab==='pending'?' · 勾选仅限已查证记录':''}`;
    work.querySelector('[data-review="previous"]').disabled=state.page===0;work.querySelector('[data-review="next"]').disabled=(state.page+1)*50>=state.total;
    updateSelection();
  }
  async function loadList() {
    const seq=++state.sequence;message('正在读取…');
    try {
      const params=new URLSearchParams(new FormData(filters));params.set('tab',state.tab);params.set('page',state.page);
      const result=await api(state.tab==='annual'?'/annual':`/records?${params}`);
      if (seq!==state.sequence || !state.loggedIn) return;
      if (state.tab==='annual') {
        state.years=result.items.filter(y=>(!params.get('security_id')||y.security_id===params.get('security_id'))&&(!params.get('report_period')||String(y.fiscal_year)===params.get('report_period').slice(0,4)));
        state.total=state.years.length;state.items=state.years.slice(state.page*50,state.page*50+50);
      } else {state.items=result.items;state.total=result.total;}
      renderList();message('仅处理当前事实版本；参考值不会在确认前替换生效值。');
    } catch(error) {if (seq!==state.sequence)return;work.querySelector('#review-list').innerHTML='<div class="review-empty review-error">数据读取失败，请刷新重试。</div>';message(friendly(error),true);}
  }
  async function openWork() {
    if (!state.loggedIn)return;
    work.showModal();message('正在读取…');
    try {await Promise.all([refreshSummary(),refreshFilters(),loadList()]);}catch(error){message(friendly(error),true);}
  }
  function evidenceOptions(sources,current) {
    return '<option value="">请选择已查阅的来源依据</option>'+sources.filter(s=>s.document_url&&s.content_hash).map(s=>`<option value="${esc(s.source_document_id)}" ${s.source_document_id===current?'selected':''}>${esc(s.report_period||'')} · ${esc(s.title||s.document_type||s.source)} · ${esc(s.announcement_date||'日期未记录')}</option>`).join('');
  }
  function evidenceFields(sources,current) {
    return `<label class="review-field">来源依据<select name="evidence_id" required>${evidenceOptions(sources,current)}</select></label><div id="review-evidence-link" class="review-link-box"></div><button class="review-link" type="button" data-action="register-evidence">登记正式公告链接</button><label class="review-field">处理说明<textarea name="note" maxlength="1000" required placeholder="记录查证位置、差异原因或采用依据"></textarea></label><label class="review-check"><input name="reviewed" type="checkbox" required>我已查阅对应报告期和口径的来源依据</label>`;
  }
  function detailHead(title,subtitle) {return `<div class="review-head"><div><h2 id="review-detail-title">${esc(title)}</h2><p>${esc(subtitle)}</p></div><button class="btn" data-action="close">关闭</button></div>`;}
  function showEvidenceLink() {
    const selected=drawer.querySelector('[name=evidence_id]')?.value;
    const source=state.detail?.sources?.find(s=>s.source_document_id===selected);
    const box=drawer.querySelector('#review-evidence-link');if(box)box.innerHTML=source?link(source.document_url,'打开来源依据')+`<div class="review-muted">指纹 ${esc(source.content_hash)}</div>`:'';
  }
  async function openDetail(item) {
    const generation=++state.detailSequence;
    state.detail=null;drawer.innerHTML=detailHead(item.name,'正在读取当前版本…')+'<div class="review-body">加载中…</div>';drawer.showModal();
    const selected=item.id;
    try {
      const detail=await api(`/detail/${item.kind}/${item.id}`);if(!drawer.open||drawer.dataset.item!==selected||generation!==state.detailSequence)return;
      state.detail=detail;renderDetail();
    } catch(error) {if(drawer.open)drawer.innerHTML=detailHead('读取失败',friendly(error))+'<div class="review-body"><button class="btn" data-action="close">关闭后刷新列表</button></div>';}
  }
  function renderDetail() {
    const detail=state.detail,item=detail.item,data=item.review_data||{},ref=data.reference||{};
    const active=detail.jobs.some(j=>j.state==='queued'||j.state==='running'&&new Date(j.lease_until)>new Date());
    const verified=item.verification_status==='verified';
    drawer.innerHTML=detailHead(item.name,`${item.symbol} · 报告期 ${item.report_period} · ${item.metric_name} · 事实 v${item.version}`)+`<div class="review-body">${tag(item)} <span class="review-muted">${esc(data.reason||'')}</span><div class="review-compare"><div class="review-box"><h3>当前候选 / 采用值</h3><b>${esc(item.value??'—')}</b> ${unit(item.unit)}<p>${esc(item.source)}</p>${link(item.document_url,'查看原始来源')}<p>公告日期 ${esc(item.announcement_date||'—')}</p></div><div class="review-box"><h3>参考值</h3><b>${esc(ref.value??'—')}</b> ${unit(ref.unit)}<p>${esc(ref.source||'尚未取得')}</p><p>校验 ${time(data.checked_at)}</p><p>差异 ${fmt(ref.difference)} · ${ratio(ref.difference_ratio)}</p></div></div><details><summary>来源及完整口径</summary><pre class="review-audit">${esc(JSON.stringify({unit:item.unit,period_type:item.period_type,fields:item.fields,reference:ref},null,2))}</pre></details>
      ${verified?'<p class="review-success">该版本已核实。后续来源变化会自动进入重新核实流程。</p>':`<div class="review-actions"><button class="btn" data-action="retry" ${active?'disabled':''}>${active?'已排队 / 校验中':'重新自动校验'}</button><button class="btn" data-action="metadata" ${active?'disabled':''}>补齐 / 纠正口径</button><button class="btn" data-action="reload-detail">刷新当前状态</button></div>${active?'<p class="review-muted">等待本地执行器处理；离线时保留排队状态。执行中不接受人工覆盖。</p>':''}<form id="review-adopt-form"><h3 class="review-section-title">人工处理</h3><label class="review-field">采用方式<select name="mode"><option value="candidate">保留候选值</option><option value="reference" ${ref.value==null?'disabled':''}>采用参考值</option><option value="custom">手动修正</option></select></label><label class="review-field">拟采用值（${unit(item.unit)}，完整精度）<input name="value" value="${esc(item.value)}" readonly required inputmode="decimal"></label>${evidenceFields(detail.sources,item.source_document_id)}<div class="review-actions"><button class="btn" type="button" data-action="ready" ${active||!item.metadata_complete?'disabled':''}>标记已查证，稍后批量确认</button><button class="btn primary" type="submit" ${active||!item.metadata_complete?'disabled':''}>确认并核实</button></div>${!item.metadata_complete?'<p class="review-error">必要口径不完整，请先补齐口径；数值相等也不能替代口径确认。</p>':''}</form>`}
      <h3 class="review-section-title">校验与操作记录（最近100条）</h3><ul class="review-timeline">${detail.events.map(event=>`<li>${esc(({verification:'自动校验',confirm:'人工确认',metadata:'口径调整',mark_ready:'已查证',attempt:'校验任务',change:'事实或年度变化'})[event.event_type]||event.event_type)} · ${esc(event.data.reason||event.data.note||event.data.status||'')}<time>${time(event.created_at)}</time><details><summary>查看依据与原值</summary><pre class="review-audit">${esc(JSON.stringify(event.data,null,2))}</pre></details></li>`).join('')||'<li>尚无操作记录；历史已核验事实保留原核验状态。</li>'}</ul><div id="review-detail-error" class="review-error" role="alert"></div></div>`;
    showEvidenceLink();
  }
  function readAdoption() {
    const form=drawer.querySelector('#review-adopt-form');if(!form.reportValidity())return null;
    const item=state.detail.item,values=Object.fromEntries(new FormData(form));
    if (!/^[+-]?\d+(?:\.\d{1,14})?$/.test(values.value)) throw new Error('请输入有限十进制数；不要输入千分位、NaN或无穷值');
    return {kind:item.kind,id:item.id,security_id:item.security_id,fact_hash:item.fact_hash,...values,reviewed:values.reviewed==='on'};
  }
  function ask(title,html,action) {
    state.confirm=action;confirm.innerHTML=`<div class="review-head"><h2 id="review-confirm-title">${esc(title)}</h2></div><div class="review-body">${html}<div id="review-confirm-error" class="review-error" role="alert"></div></div><div class="review-foot"><button class="btn" data-confirm="cancel">取消</button><button class="btn primary" data-confirm="submit">确认</button></div>`;confirm.showModal();
  }
  async function mutate(path,body,success) {
    setBusy(true);
    try {const result=await api(path,body);confirm.close();drawer.close();state.selected.clear();if(path==='/mark_ready')body.items.forEach(item=>state.selected.add(item.id));message(success);await Promise.all([refreshSummary(),loadList()]);return result;}
    finally{setBusy(false);}
  }
  async function annualDetail(item) {
    const generation=++state.detailSequence;
    drawer.innerHTML=detailHead(`${item.name} · 年度查全`,`${item.fiscal_year}报告年度`);drawer.showModal();
    const result=await api(`/sources?security_id=${encodeURIComponent(item.security_id)}`);if(!drawer.open||generation!==state.detailSequence)return;
    state.detail={annual:item,sources:result.items};
    const available=item.ready&&item.status!=='complete'&&!item.unresolved;
    drawer.innerHTML=detailHead(`${item.name} · 年度查全`,`${item.fiscal_year}报告年度 · 版本 ${item.version}`)+`<div class="review-body"><p>单笔分红核实通过不代表全年查全。请核对该报告年度所有方案、变更及不分配公告。</p><p>已发现正分红方案 ${esc(item.positive_plans)} 项 · 未处理分红 ${esc(item.unresolved)} 项</p>${!item.ready?'<p class="review-muted">等待年报披露，不计入需处理提醒。</p>':''}${item.unresolved?'<p class="review-muted">仍有候选或未批准方案，请先处理单笔核实或等待正式批准。</p>':''}${item.status==='complete'?`<p class="review-success">全年已确认 · ${time(item.reviewed_at)}</p><p>${esc(item.review_note||'')}</p>`:''}<form id="review-annual-form"><label class="review-field">全年结论<select name="conclusion"><option value="with_dividend">全年方案已查全，有现金分红</option><option value="zero">正式依据明确全年无现金分红</option></select></label>${evidenceFields(result.items,item.source_document_id)}<label class="review-check"><input name="scope_checked" type="checkbox" required>已覆盖全年所有方案及后续变更</label><label class="review-check"><input name="zero_confirmed" type="checkbox">选择全年零分红时，已找到明确不分配依据（不能以查不到方案代替）</label><button class="btn primary" type="submit" ${available?'':'disabled'}>确认年度查全</button></form><div id="review-detail-error" class="review-error" role="alert"></div></div>`;showEvidenceLink();
  }
  async function metadataForm() {
    const detail=state.detail,item=detail.item,f=item.fields||{};
    drawer.innerHTML=detailHead('补齐 / 纠正口径',`${item.name} · 原报告期 ${item.report_period} · 原值 ${item.value} ${unit(item.unit)}`)+`<form class="review-body" id="review-metadata-form"><p class="review-muted">保存为新候选版本，旧参考回执不会沿用；不会直接核实通过。</p><div class="review-grid"><label class="review-field">报告期<input name="report_period" type="date" required value="${esc(item.report_period)}"></label><label class="review-field">原始来源数值<input name="value" required inputmode="decimal" value="${esc(item.value)}"></label><label class="review-field">单位<select name="unit"><option value="CNY">元</option><option value="万元">万元</option><option value="百万元">百万元</option><option value="亿元">亿元</option><option value="percent">%</option><option value="CNY/share">元/股</option></select></label><label class="review-field">币种<select name="currency"><option value="CNY">人民币 CNY</option></select></label><label class="review-field">统计口径<select name="period_type"><option value="year_to_date">年初至今累计</option><option value="annual">全年累计</option><option value="event">税前单笔分红</option></select></label></div>
      ${item.kind==='dividend'?`<div class="review-grid"><label class="review-field">稳定方案标识<input name="plan_key" required value="${esc(f.plan_key||'')}" placeholder="例如：股票:2025:年度分红"></label><label class="review-field">关联原方案ID（同方案才填写）<input name="supersedes_id" placeholder="无关联则留空"></label><label class="review-field">阶段<select name="event_stage"><option value="proposal">预案</option><option value="approved">已批准未实施</option><option value="implemented">已实施</option></select></label><label class="review-field">现金分红总额（元）<input name="cash_dividend_total" required value="${esc(f.cash_dividend_total)}" inputmode="decimal"></label><label class="review-field">除息日<input name="ex_date" type="date" value="${esc(f.ex_date||'')}"></label><label class="review-field">批准依据<select name="approval_basis"><option value="">未取得</option><option value="shareholder_resolution">股东大会决议</option><option value="shareholder_authorized_board">股东授权董事会</option></select></label><label class="review-field">每股送股比例<input name="bonus_shares_per_share" required value="${esc(f.bonus_shares_per_share??0)}" inputmode="decimal"></label><label class="review-field">每股转增比例<input name="transfer_shares_per_share" required value="${esc(f.transfer_shares_per_share??0)}" inputmode="decimal"></label></div><details><summary>查看该股票当前方案，核对跨阶段关联</summary><div id="review-plans">正在读取…</div></details>`:''}
      ${evidenceFields(detail.sources,item.source_document_id)}<button class="btn primary" type="submit">保存为新候选</button><button class="btn" data-action="back-detail" type="button">取消</button><div id="review-detail-error" class="review-error" role="alert"></div></form>`;
    drawer.querySelector('[name=unit]').value=item.unit;drawer.querySelector('[name=period_type]').value=item.period_type;
    if(item.kind==='dividend') {drawer.querySelector('[name=event_stage]').value=f.event_stage;drawer.querySelector('[name=approval_basis]').value=f.approval_basis||'';try{const result=await api(`/plans?security_id=${encodeURIComponent(item.security_id)}`);if(drawer.querySelector('#review-plans'))drawer.querySelector('#review-plans').innerHTML=result.items.map(p=>`<p>${esc(p.report_period)} · ${esc(p.fields.plan_key||'未关联')} · ${esc(p.fields.event_stage)}<br>ID ${esc(p.id)}</p>`).join('')||'暂无其他方案';}catch(error){drawer.querySelector('#review-plans').textContent=friendly(error);}}
    showEvidenceLink();
  }
  function registerEvidence() {
    const item=state.detail.item||state.detail.annual;
    ask('登记正式公告',`<form id="review-evidence-form"><label class="review-field">正式公告链接<input name="url" type="url" required placeholder="https://static.cninfo.com.cn/…"></label><label class="review-field">标题<input name="title" maxlength="200" required></label><label class="review-field">公告类型<select name="document_type"><option value="annual_report">年度报告</option><option value="quarterly_report">季度报告</option><option value="semi_annual_report">半年度报告</option><option value="dividend_announcement">分红公告</option></select></label><label class="review-field">报告期<input name="report_period" type="date" value="${esc(item.report_period||item.fiscal_year+'-12-31')}" required></label><label class="review-field">公告日期<input name="announcement_date" type="date" required></label></form><p class="review-muted">仅接受巨潮、上交所、深交所正式链接；保存内容指纹。仍需人工查阅证券及报告期。</p>`,async()=>{
      const form=confirm.querySelector('form');if(!form.reportValidity())return;
      const values=Object.fromEntries(new FormData(form));const result=await api('/evidence',{...values,security_id:item.security_id});
      state.detail.sources.unshift({...values,source_document_id:result.source_document_id,document_url:values.url,content_hash:'已存档',source:'official-review'});
      const select=drawer.querySelector('[name=evidence_id]');select.innerHTML=evidenceOptions(state.detail.sources,result.source_document_id);showEvidenceLink();confirm.close();
    });
  }
  drawer.addEventListener('close',()=>state.detailSequence++);
  entry.addEventListener('click',()=>openWork());
  filters.addEventListener('submit',event=>{event.preventDefault();state.page=0;state.selected.clear();loadList();});
  work.addEventListener('change',event=>{
    if(event.target.matches('[data-review-select]')){event.target.checked?state.selected.add(event.target.dataset.reviewSelect):state.selected.delete(event.target.dataset.reviewSelect);updateSelection();}
    if(event.target.id==='review-select-all'){state.selected.clear();if(event.target.checked)state.items.filter(i=>i.review_data?.ready&&i.verification_status!=='verified').forEach(i=>state.selected.add(i.id));renderList();}
  });
  work.addEventListener('click',async event=>{
    const target=event.target.closest('button');if(!target||state.busy)return;
    try {
      if(target.dataset.reviewTab){state.tab=target.dataset.reviewTab;state.page=0;state.selected.clear();work.querySelectorAll('[role=tab]').forEach(t=>t.setAttribute('aria-selected',String(t===target)));await loadList();return;}
      if(target.dataset.detailIndex!==undefined){const item=state.items[Number(target.dataset.detailIndex)];drawer.dataset.item=item.id;await openDetail(item);return;}
      if(target.dataset.annualIndex!==undefined){await annualDetail(state.items[Number(target.dataset.annualIndex)]);return;}
      const action=target.dataset.review;
      if(action==='close-work')work.close();
      if(action==='refresh'){state.selected.clear();await Promise.all([refreshSummary(),loadList()]);}
      if(action==='previous'||action==='next'){state.page+=action==='next'?1:-1;state.selected.clear();await loadList();}
      if(action==='recheck-all') ask('自动校验全部待核实',`<p>将为当前范围的 ${esc(state.summary?.pending_facts??'—')} 项未核实事实排队，包含其他页的数据；活动任务复用。年度查全仍需人工查阅。</p><p class="review-muted">本地执行器离线时保持排队；不会自动确认未知口径。</p>`,()=>mutate('/enqueue-all',{},'待核实事实已加入自动校验队列。'));
      if(action==='publish') ask('更新看板',`<p>将以当前已核实事实重新计算并更新看板；未核实项保持缺数。当前待更新 ${esc(state.summary.pending_publish)} 项。</p><p class="review-muted">任务在云端运行；失败可以重试，不回退核实记录。</p>`,async()=>{
        const result=await mutate('/publish',{},'已提交云端更新请求。');if(result.dispatch_error)message(result.dispatch_error,true);
      });
      if(action==='batch'){
        const selected=state.items.filter(i=>state.selected.has(i.id));
        ask('批量确认已查证记录',`<p>本次仅处理当前页已勾选的 ${selected.length} 项，保留候选值。</p>${selected.map(i=>`<p>${esc(i.name)} · ${esc(i.report_period)} · ${esc(i.metric_name)} · ${esc(i.value)} ${unit(i.unit)}</p>`).join('')}<p class="review-muted">任一条版本或状态变化，整批不写入，请刷新后重试。</p>`,()=>mutate('/confirm',{items:selected.map(i=>({kind:i.kind,id:i.id,security_id:i.security_id,fact_hash:i.fact_hash,mode:'candidate',reviewed:true,note:i.review_data.note,evidence_id:i.review_data.evidence_id}))},'批量核实成功。'));
      }
    }catch(error){message(friendly(error),true);}
  });
  drawer.addEventListener('change',event=>{
    if(event.target.name==='evidence_id')showEvidenceLink();
    if(event.target.name==='mode') {drawer.querySelector('[data-action=ready]').disabled=event.target.value!=='candidate'||!state.detail.item.metadata_complete;const input=drawer.querySelector('[name=value]');input.readOnly=event.target.value!=='custom';if(event.target.value!=='custom')input.value=event.target.value==='candidate'?state.detail.item.value:state.detail.item.review_data.reference.value;}
  });
  drawer.addEventListener('click',async event=>{
    const action=event.target.closest('[data-action]')?.dataset.action;if(!action||state.busy)return;
    try {
      if(action==='close'){drawer.close();return;}
      if(action==='back-detail')renderDetail();
      if(action==='metadata')await metadataForm();
      if(action==='register-evidence')registerEvidence();
      if(action==='reload-detail'){const item=state.detail.item;state.detail=await api(`/detail/${item.kind}/${item.id}`);renderDetail();}
      if(action==='ready'){const item=readAdoption();if(item&&item.mode!=='candidate')throw new Error('批量处理仅支持保留候选值');if(item)await mutate('/mark_ready',{items:[item]},'已标记查证，可在列表批量确认。');}
      if(action==='retry'){const item=state.detail.item;setBusy(true);try{await api('/enqueue',{items:[{kind:item.kind,id:item.id}]});state.detail=await api(`/detail/${item.kind}/${item.id}`);renderDetail();message('已排队，等待本地执行器处理。');}finally{setBusy(false);}}
    }catch(error){drawer.querySelector('#review-detail-error')?.replaceChildren(document.createTextNode(friendly(error)));}
  });
  drawer.addEventListener('submit',event=>{
    event.preventDefault();if(state.busy)return;
    try {
      if(event.target.id==='review-adopt-form'){
        const item=readAdoption();if(!item)return;
        ask('确认并核实',`<p>${esc(state.detail.item.name)} · ${esc(state.detail.item.report_period)} · ${esc(state.detail.item.metric_name)}</p><p>原值 ${esc(state.detail.item.value)} → 采用值 <strong>${esc(item.value)}</strong> ${unit(state.detail.item.unit)}</p><p>${esc(item.note)}</p>`,()=>mutate('/confirm',{items:[item]},'人工核实成功。'));
      }
      if(event.target.id==='review-metadata-form'){
        if(!event.target.reportValidity())return;const values=Object.fromEntries(new FormData(event.target)),item=state.detail.item;
        const {evidence_id,note,reviewed,...changes}=values;Object.keys(changes).forEach(key=>{if(changes[key]==='')delete changes[key];});
        ask('保存新的候选版本',`<p>${esc(item.name)} · ${esc(item.metric_name)}</p><p>原报告期 ${esc(item.report_period)} → ${esc(changes.report_period)}</p><p>原值 ${esc(item.value)} → ${esc(changes.value)}</p><p>旧校验回执失效；保存后仍需重新核实。</p>`,()=>mutate('/metadata',{items:[{kind:item.kind,id:item.id,security_id:item.security_id,fact_hash:item.fact_hash,evidence_id,note,reviewed:reviewed==='on',changes}]},'已保存新候选，请重新核实。'));
      }
      if(event.target.id==='review-annual-form'){
        if(!event.target.reportValidity())return;const values=Object.fromEntries(new FormData(event.target)),item=state.detail.annual;
        ask('确认年度查全',`<p>${esc(item.name)} · ${esc(item.fiscal_year)}报告年度</p><p>${values.conclusion==='zero'?'确认全年无现金分红':'确认全年分红方案已查全'}</p><p>${esc(values.note)}</p>`,()=>mutate('/annual',{...values,security_id:item.security_id,fiscal_year:item.fiscal_year,version:item.version,scope_checked:values.scope_checked==='on',zero_confirmed:values.zero_confirmed==='on'},'年度查全成功。'));
      }
    }catch(error){drawer.querySelector('#review-detail-error')?.replaceChildren(document.createTextNode(friendly(error)));}
  });
  confirm.addEventListener('click',async event=>{
    const action=event.target.closest('[data-confirm]')?.dataset.confirm;if(!action||state.busy)return;
    if(action==='cancel'){confirm.close();return;}
    setBusy(true);try{await state.confirm?.();}catch(error){confirm.querySelector('#review-confirm-error').textContent=friendly(error);}finally{setBusy(false);}
  });
  [work,drawer,confirm].forEach(dialog=>dialog.addEventListener('cancel',event=>{if(state.busy)event.preventDefault();}));
  window.addEventListener('trend-dashboard-overview',event=>{
    state.loggedIn=true;entry.hidden=false;
    if(event.detail.review){state.summary=event.detail.review;summaryView();}
    else{state.summary=null;summaryView();}
  });
  window.addEventListener('trend-dashboard-signed-out',()=>{state.loggedIn=false;state.sequence++;state.summary=null;entry.hidden=true;work.close();drawer.close();confirm.close();});
  setInterval(async()=>{
    if(!state.loggedIn||document.hidden||state.busy)return;
    try{
      await refreshSummary();
      if(work.open&&!drawer.open&&!confirm.open)await loadList();
    }catch(_){state.summary=null;summaryView();if(work.open)message('核实状态读取失败，请刷新重试。',true);}
  },15000);
})();
