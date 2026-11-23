import {assertEquals,assert,assertRejects} from 'jsr:@std/assert@1';
import {applyReviewInvalidations} from '../_shared/review.ts';

let handler: (request:Request)=>Promise<Response>;
const oldServe=Deno.serve,oldFetch=globalThis.fetch;
const names=['SUPABASE_URL','SUPABASE_PUBLISHABLE_KEYS','SUPABASE_SECRET_KEYS','REVIEW_GITHUB_TOKEN','REVIEW_GITHUB_REPOSITORY'];
const oldEnv=names.map(n=>Deno.env.get(n));
Deno.env.set('SUPABASE_URL','https://review-test.invalid');
Deno.env.set('SUPABASE_PUBLISHABLE_KEYS',JSON.stringify({default:'sb_publishable_test'}));
Deno.env.set('SUPABASE_SECRET_KEYS',JSON.stringify({default:'sb_secret_test'}));
Deno.env.delete('REVIEW_GITHUB_TOKEN');Deno.env.delete('REVIEW_GITHUB_REPOSITORY');
Deno.serve=((callback:typeof handler)=>{handler=callback;return {} as Deno.HttpServer;}) as typeof Deno.serve;
let calls:Array<{url:URL,body:Record<string,unknown>}> = [], conflict=false, authFailure=false;
const job={job_id:'11111111-1111-1111-1111-111111111111',attempt_seq:2,state:'queued',lease_until:null,lease_token:'must-not-leak'};
globalThis.fetch=(async(input:RequestInfo|URL,init?:RequestInit)=>{
  const url=new URL(input instanceof Request?input.url:String(input));
  if(url.origin!=='https://review-test.invalid')throw new Error('Unexpected network destination');
  const body=JSON.parse(String(init?.body||'{}'));calls.push({url,body});
  const json=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json','Content-Range':'0-49/117'}});
  if(url.pathname==='/auth/v1/user')return authFailure?json({message:'expired'},401):json({id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',app_metadata:{}});
  if(url.pathname==='/rest/v1/rpc/review_reap')return json(null);
  if(url.pathname==='/rest/v1/rpc/review_overview')return json({pending_facts:0,pending_years:0,pending_publish:1,latest_job:job});
  if(url.pathname==='/rest/v1/rpc/review_command')return conflict?json({message:'review_version_changed',code:'P0001'},400):json(body.p_action==='publish'?job:[]);
  if(url.pathname==='/rest/v1/rpc/review_prepare_dispatch')return json(true);
  if(url.pathname==='/rest/v1/rpc/review_publish_dispatched')return json(null);
  if(url.pathname==='/rest/v1/quarterly_review_records')return json([]);
  throw new Error(`Unmocked ${url.pathname}`);
}) as typeof fetch;
const module=await import('./index.ts');Deno.serve=oldServe;
function request(path:string,body?:unknown,auth=true){return new Request('https://review-test.invalid/functions/v1/quarterly-review'+path,{method:body===undefined?'GET':'POST',headers:{...(auth?{Authorization:'Bearer local-test'}:{}),'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});}
function reset(){calls=[];conflict=false;authFailure=false;}

Deno.test('review requires login, with no business role gate',async()=>{
  reset();let response=await handler(request('/summary',undefined,false));assertEquals(response.status,401);await response.body?.cancel();
  response=await handler(request('/summary'));assertEquals(response.status,200);
  const data=await response.json();assertEquals(data.pending_facts,0);assertEquals(data.latest_job.lease_token,undefined);
  authFailure=true;response=await handler(request('/summary'));assertEquals(response.status,401);await response.body?.cancel();
});
Deno.test('record pages carry the exact filters and a bounded range',async()=>{
  reset();const response=await handler(request('/records?tab=pending&page=2&report_period=2024-12-31&status=diff'));
  assertEquals(response.status,200);const data=await response.json();assertEquals(data.total,117);
  const url=calls.find(c=>c.url.pathname.endsWith('/quarterly_review_records'))!.url;
  assertEquals(url.searchParams.get('offset'),'100');assertEquals(url.searchParams.get('limit'),'50');
  assertEquals(url.searchParams.get('report_period'),'eq.2024-12-31');assertEquals(url.searchParams.get('status'),'eq.diff');
});
Deno.test('manual operations bind actor to auth and return transaction conflicts',async()=>{
  reset();conflict=true;const response=await handler(request('/confirm',{items:[],actor:'spoof'}));
  assertEquals(response.status,409);assertEquals((await response.json()).error,'review_version_changed');
  assertEquals(calls.find(c=>c.url.pathname.endsWith('/review_command'))!.body.p_actor,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
});
Deno.test('browser cannot submit worker success or alter verification state directly',async()=>{
  reset();const response=await handler(request('/review_complete',{status:'auto'}));assertEquals(response.status,400);await response.body?.cancel();
  assert(!calls.some(c=>c.url.pathname.endsWith('/review_command')));
});
Deno.test('missing cloud configuration is a persisted failure, never a success toast',async()=>{
  reset();const response=await handler(request('/publish',{}));assertEquals(response.status,200);const data=await response.json();
  assertEquals(data.dispatch_error,'云端发布尚未配置');assertEquals(data.job.lease_token,undefined);
  assertEquals(calls.find(c=>c.url.pathname.endsWith('/review_publish_dispatched'))!.body.p_error,'云端发布尚未配置');
});
Deno.test('official evidence rejects private and arbitrary hosts before making a request',async()=>{
  reset();for(const url of ['http://127.0.0.1/x','https://example.com/a','https://static.cninfo.com.cn.evil.test/a','https://user:pass@static.cninfo.com.cn/a']){
    await assertRejects(()=>module.evidenceBytes(url),Error,'review_official_url_required');
  }assertEquals(calls.length,0);
});
Deno.test('live invalidation blanks affected cumulative dependencies but preserves other cells and quotes',()=>{
  const original={security_id:'sid',symbol:'sh600000',close:20,fundamental_status:'稳健',dividend_safety_status:'稳健',quarterly:{review_contract:1,metrics:{a:{value:100,review_dependencies:['changed'],review_values:['value']},b:{value:50,review_dependencies:['unchanged'],review_values:['value']}}}};
  const [asset]=applyReviewInvalidations([original],{invalidations:[{security_id:'sid',object_key:'changed'}]});
  const q=asset.quarterly as {metrics:{a:{value:null,status:string},b:{value:number}}};
  assertEquals(q.metrics.a.value,null);assertEquals(q.metrics.a.status,'更新待确认');assertEquals(q.metrics.b.value,50);assertEquals(asset.close,20);assertEquals(asset.fundamental_status,null);assertEquals(asset.dividend_safety_status,null);
  assertEquals(original.quarterly.metrics.a.value,100);
  assertEquals(applyReviewInvalidations([{symbol:'sh600000',quarterly:{years:[2024]}}],{invalidations:[{symbol:'sh600000'}]})[0].quarterly,null);
});
Deno.test('restore review test process boundaries',()=>{
  globalThis.fetch=oldFetch;names.forEach((n,i)=>oldEnv[i]===undefined?Deno.env.delete(n):Deno.env.set(n,oldEnv[i]!));
});
