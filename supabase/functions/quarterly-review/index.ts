import { authorizedClients, corsHeaders } from '../_shared/supabase.ts';
import { safeJob, safeSummary } from '../_shared/review.ts';

function json(req: Request, body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {status, headers:{...corsHeaders(req), 'Content-Type':'application/json'}});
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const officialHosts = new Set(['static.cninfo.com.cn','www.cninfo.com.cn','www.sse.com.cn','www.szse.cn','disc.static.szse.cn']);
export async function evidenceBytes(raw: string): Promise<Uint8Array> {
  let url = new URL(raw);
  for (let redirects = 0; redirects < 4; redirects++) {
    if (url.protocol !== 'https:' || url.port || url.username || url.password || !officialHosts.has(url.hostname)) throw new Error('review_official_url_required');
    const response = await fetch(url, {redirect:'manual', signal:AbortSignal.timeout(20000)});
    if ([301,302,303,307,308].includes(response.status)) {
      await response.body?.cancel();
      url = new URL(response.headers.get('location') || '', url);
      continue;
    }
    if (!response.ok || !response.body) throw new Error('review_evidence_fetch_failed');
    const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
    try {
      for (;;) {
        const {value,done} = await reader.read(); if (done) break;
        size += value.length; if (size > 12 * 1024 * 1024) throw new Error('review_evidence_too_large');
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
    if (!size) throw new Error('review_empty_evidence');
    const bytes = new Uint8Array(size); let offset = 0;
    for (const part of chunks) {bytes.set(part, offset); offset += part.length;}
    return bytes;
  }
  throw new Error('review_evidence_redirect_limit');
}

async function sourcesFor(admin: ReturnType<typeof import('../_shared/supabase.ts').supabaseClients>['admin'], security: string) {
  const rows: Array<Record<string,unknown>>=[];
  for (let offset=0;;offset+=500) {
    const page=await admin.from('source_documents').select('source_document_id,title,source,document_type,report_period,document_url,content_hash,announcement_date,fetched_at').eq('security_id',security).order('fetched_at',{ascending:false}).order('source_document_id').range(offset,offset+499);
    if(page.error) throw new Error('review_sources_failed');
    rows.push(...(page.data||[]));if((page.data||[]).length<500)break;
  }
  return {data:rows,error:null};
}

async function dispatchPublish(admin: ReturnType<typeof import('../_shared/supabase.ts').supabaseClients>['admin'], job: Record<string, unknown>) {
  let error: string | null = null;
  try {
    const token = Deno.env.get('REVIEW_GITHUB_TOKEN');
    const repo = Deno.env.get('REVIEW_GITHUB_REPOSITORY');
    if (!token || !repo || !/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Error('云端发布尚未配置');
    const response = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/trend_observer_v2.yml/dispatches`, {
      method:'POST', headers:{Authorization:`Bearer ${token}`, Accept:'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'},
      body:JSON.stringify({ref:Deno.env.get('REVIEW_GITHUB_REF') || 'main', inputs:{command:'review-publish', review_job:job.job_id, review_attempt:String(job.attempt_seq)}}),
      signal:AbortSignal.timeout(15000)
    });
    if (!response.ok) throw new Error(`云端发布触发失败（${response.status}）`);
    await response.body?.cancel();
  } catch (exc) {error = exc instanceof Error ? exc.message : '云端发布触发失败';}
  const saved = await admin.rpc('review_publish_dispatched',{p_job:job.job_id,p_attempt:job.attempt_seq,p_error:error});
  if (saved.error) throw new Error('review_dispatch_status_failed');
  return error;
}

Deno.serve(async request => {
  if (request.method === 'OPTIONS') return new Response('ok',{headers:corsHeaders(request)});
  if (!['GET','POST'].includes(request.method)) return json(request,{error:'method_not_allowed'},405);
  const header = request.headers.get('Authorization'); if (!header) return json(request,{error:'unauthorized'},401);
  const auth = await authorizedClients(header); if ('error' in auth) return json(request,{error:auth.error},auth.status);
  const {admin,user} = auth; const url = new URL(request.url);
  const path = url.pathname.replace(/^.*quarterly-review/,'') || '/summary';
  try {
    if (request.method === 'GET') {
      if (path === '/summary') {
        const reaped = await admin.rpc('review_reap'); if (reaped.error) throw new Error('review_status_failed');
        const result = await admin.rpc('review_overview'); if (result.error) throw new Error('review_status_failed');
        return json(request,safeSummary(result.data));
      }
      if (path === '/annual') {
        const result = await admin.rpc('review_annual_records'); if (result.error) throw new Error('review_annual_failed');
        return json(request,{items:result.data});
      }
      if (path === '/records') {
        const page = Math.max(0,Math.min(10000,Number(url.searchParams.get('page')) || 0));
        let query = admin.from('quarterly_review_records').select('*',{count:'exact'});
        const tab = url.searchParams.get('tab') || 'pending';
        if (tab === 'pending') query = query.neq('verification_status','verified');
        else if (tab === 'auto') query = query.eq('status','auto');
        else if (tab === 'manual') query = query.in('status',['manual','legacy']);
        else return json(request,{error:'invalid_tab'},400);
        for (const key of ['security_id','report_period','status']) {
          const value = url.searchParams.get(key); if (value) query = query.eq(key,value);
        }
        const search = (url.searchParams.get('search') || '').trim().slice(0,100).replace(/[%_\\]/g,'');
        if (search) query = query.ilike('metric_name',`%${search}%`);
        if (tab==='pending') query=query.order('status_order');
        const result = await query.order('review_updated_at',{ascending:false}).order('id').range(page*50,page*50+49);
        if (result.error) throw new Error('review_records_failed');
        return json(request,{items:result.data,total:result.count,page,page_size:50});
      }
      if (path === '/filters') {
        const result = await admin.from('securities').select('security_id,symbol,name').eq('is_active',true).eq('asset_type','股票').order('name');
        if (result.error) throw new Error('review_filters_failed');
        const periods = await admin.rpc('review_periods'); if (periods.error) throw new Error('review_filters_failed');
        return json(request,{stocks:result.data,periods:periods.data});
      }
      if (path === '/plans' && uuid.test(url.searchParams.get('security_id') || '')) {
        const result = await admin.from('quarterly_review_records').select('id,report_period,fields').eq('security_id',url.searchParams.get('security_id')!).eq('kind','dividend').order('report_period',{ascending:false}).range(0,499);
        if (result.error) throw new Error('review_plans_failed');
        return json(request,{items:result.data});
      }
      const detail = path.match(/^\/detail\/(financial|dividend)\/([a-f0-9-]+)$/);
      if (detail && uuid.test(detail[2])) {
        const result = await admin.rpc('review_item',{p_kind:detail[1],p_id:detail[2]});
        if (result.error) return json(request,{error:'review_version_changed'},409);
        const item = result.data;
        const [events,sources,jobs] = await Promise.all([
          admin.rpc('review_history',{p_kind:detail[1],p_id:detail[2]}),
          sourcesFor(admin,item.security_id),
          admin.from('review_jobs').select('job_id,state,attempt_seq,updated_at,lease_until,error').eq('fact_id',item.id).order('updated_at',{ascending:false}).limit(10)
        ]);
        if ([events,sources,jobs].some(x=>x.error)) throw new Error('review_detail_failed');
        return json(request,{item,events:events.data,sources:sources.data,jobs:jobs.data});
      }
      // Annual evidence picker is bounded to the selected issuer, with complete pagination.
      if (path === '/sources' && uuid.test(url.searchParams.get('security_id') || '')) {
        const result = await sourcesFor(admin,url.searchParams.get('security_id')!);
        if (result.error) throw new Error('review_sources_failed');
        return json(request,{items:result.data});
      }
      return json(request,{error:'not_found'},404);
    }
    if (Number(request.headers.get('Content-Length')) > 200000) return json(request,{error:'request_too_large'},413);
    const raw=await request.text();
    if(raw.length>200000)return json(request,{error:'request_too_large'},413);
    let body;try{body=JSON.parse(raw);}catch(_){return json(request,{error:'invalid_json'},400);}
    if(!body||typeof body!=='object'||Array.isArray(body))return json(request,{error:'invalid_payload'},400);
    if (path === '/evidence') {
      if (!uuid.test(body.security_id) || !/^\d{4}-\d{2}-\d{2}$/.test(body.report_period) || !/^\d{4}-\d{2}-\d{2}$/.test(body.announcement_date)
        || !['annual_report','quarterly_report','semi_annual_report','dividend_announcement'].includes(body.document_type)) return json(request,{error:'invalid_evidence_fields'},400);
      const security = await admin.from('securities').select('security_id').eq('security_id',body.security_id).eq('is_active',true).eq('asset_type','股票').maybeSingle();
      if (!security.data) return json(request,{error:'asset_not_found'},404);
      const bytes = await evidenceBytes(body.url);
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new Uint8Array(bytes).buffer))).map(b=>b.toString(16).padStart(2,'0')).join('');
      const saved = await admin.from('source_documents').upsert({security_id:body.security_id,source:'official-review',source_record_id:`review:${body.security_id}:${hash}`,title:String(body.title||'正式公告核实依据').slice(0,200),document_type:body.document_type,report_period:body.report_period,announcement_date:body.announcement_date,document_url:body.url,content_hash:hash,fetched_at:new Date().toISOString()}, {onConflict:'source,source_record_id,content_hash'}).select('source_document_id').single();
      if (saved.error) throw new Error('review_evidence_save_failed');
      return json(request,saved.data);
    }
    if (path==='/enqueue-all') {
      const result=await admin.rpc('review_enqueue_all',{p_actor:user.id});
      if (result.error) return json(request,{error:result.error.message},409);
      return json(request,result.data);
    }
    const action = path.slice(1);
    if (!['enqueue','confirm','mark_ready','metadata','annual','publish'].includes(action)) return json(request,{error:'invalid_action'},400);
    const result = await admin.rpc('review_command',{p_action:action,p_payload:body,p_actor:user.id});
    if (result.error) return json(request,{error:result.error.message},409);
    if (action === 'publish') {
      const job = result.data;
      // Only the first delivery of a queued attempt triggers the cloud workflow.
      // Repeated UI requests reuse the job; the worker claim is a second fence.
      let dispatchError: string | null = null;
      if (job.state === 'queued') {
        const claim = await admin.rpc('review_prepare_dispatch',{p_job:job.job_id,p_attempt:job.attempt_seq});
        if (claim.error) throw new Error('review_dispatch_claim_failed');
        if (claim.data) dispatchError = await dispatchPublish(admin,job);
      }
      return json(request,{job:safeJob(job),dispatch_error:dispatchError});
    }
    return json(request,{result:result.data});
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : 'review_request_failed';
    return json(request,{error:message.startsWith('review_') ? message : 'review_request_failed'},500);
  }
});
