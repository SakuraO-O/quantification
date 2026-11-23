import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {PGlite} from '@electric-sql/pglite';

async function setup(t){
 const db=new PGlite();t.after(()=>db.close());
 await db.exec('create role anon;create role authenticated;create role service_role bypassrls;create schema auth;create table auth.users(id uuid primary key);');
 for(const f of readdirSync('supabase/migrations').sort())await db.exec(readFileSync('supabase/migrations/'+f,'utf8').replace('create extension if not exists pgcrypto;',''));
 await db.exec(`insert into securities(security_id,symbol,market,name,asset_type,currency) values('11111111-1111-1111-1111-111111111111','sh600000','CN','测试股份','股票','CNY');
 insert into metric_definitions(metric_code,name,unit,definition) values('net_profit','归母净利润','CNY','测试'),('revenue','营业收入','CNY','测试') on conflict do nothing;`);
 return db;
}
const sid='11111111-1111-1111-1111-111111111111';
async function rpc(db,fn,args=[]){return (await db.query(`select ${fn}(${args.map((_,i)=>'$'+(i+1)).join(',')}) as result`,args.map(v=>typeof v==='object'&&v!==null?JSON.stringify(v):v))).rows[0].result;}
async function document(db,source='formal',period='2024-12-31',date='2025-03-01'){
 return (await db.query(`insert into source_documents(security_id,source,source_record_id,document_type,report_period,announcement_date,document_url,content_hash)
 values($1,$2,gen_random_uuid()::text,$3,$4,$5,'https://static.cninfo.com.cn/test.pdf',$6) returning source_document_id`,[sid,source,source==='formal'?'annual_report':'structured_financial_snapshot',period,date,'a'.repeat(64)])).rows[0].source_document_id;
}
async function fact(db,doc,metric='net_profit',period='2024-12-31',value='100'){
 const id=(await db.query(`insert into financial_facts(security_id,report_period,metric_code,value,unit,period_type,source_document_id) values($1,$2,$3,$4,'CNY',$5,$6) returning financial_fact_id`,[sid,period,metric,value,period.endsWith('12-31')?'annual':'year_to_date',doc])).rows[0].financial_fact_id;
 return rpc(db,'review_item',['financial',id]);
}
const edit=(item,evidence,changes={})=>({kind:'financial',id:item.id,security_id:sid,fact_hash:item.fact_hash,evidence_id:evidence,note:'已核对正式公告',reviewed:true,mode:'candidate',...changes});
test('manual corrections retain the old fact and batch conflicts roll back every row',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc),b=await fact(db,doc,'revenue');
 await rpc(db,'review_command',['mark_ready',{items:[edit(a,doc)]},'user']);
 await assert.rejects(rpc(db,'review_command',['confirm',{items:[edit(a,doc),edit(b,doc,{fact_hash:'stale'})]},'user']),/version_changed/);
 assert.equal((await rpc(db,'review_item',['financial',a.id])).verification_status,'candidate');
 const [saved]=await rpc(db,'review_command',['confirm',{items:[edit(a,doc,{mode:'custom',value:'110'})]},'user']);
 assert.equal(saved.value,'110');assert.equal(saved.status,'manual');assert.notEqual(saved.id,a.id);
 assert.equal((await db.query('select value::text,is_current from financial_facts where financial_fact_id=$1',[a.id])).rows[0].is_current,false);
 await assert.rejects(rpc(db,'review_command',['confirm',{items:[edit(a,doc)]},'user']),/not_current/);
});
test('only checked current facts may be batch confirmed',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc),b=await fact(db,doc,'revenue');
 await assert.rejects(rpc(db,'review_command',['confirm',{items:[edit(a,doc),edit(b,doc)]},'user']),/not_ready/);
 await rpc(db,'review_command',['mark_ready',{items:[edit(a,doc),edit(b,doc)]},'user']);
 const saved=await rpc(db,'review_command',['confirm',{items:[edit(a,doc),edit(b,doc)]},'user']);assert.equal(saved.length,2);
 assert(saved.every(r=>r.status==='manual'));
});
test('failed attempts retry; stale attempts and leases cannot override current success',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc);
 const enqueue=()=>rpc(db,'review_enqueue',['financial',a.id,'user']);
 const first=await enqueue();assert.equal((await enqueue()).attempt_seq,1);
 const claim=await rpc(db,'review_claim',['verification']);
 await assert.rejects(rpc(db,'review_command',['confirm',{items:[edit(a,doc)]},'user']),/task_active/);
 await rpc(db,'review_complete',[first.job_id,1,claim.lease_token,{status:'failed',reason:'network'}]);
 const second=await enqueue();assert.equal(second.job_id,first.job_id);assert.equal(second.attempt_seq,2);
 const lease=await rpc(db,'review_claim',['verification']);
 const saved=await rpc(db,'review_complete',[second.job_id,2,lease.lease_token,{status:'auto',reference:{value:'100'},rule_version:'test'}]);
 assert.equal(saved.status,'auto');
 assert.equal((await rpc(db,'review_complete',[first.job_id,1,claim.lease_token,{status:'failed',reason:'late'}])).stale,true);
 assert.equal((await rpc(db,'review_item',['financial',saved.id])).status,'auto');
 assert.equal((await rpc(db,'review_complete',[second.job_id,2,lease.lease_token,{status:'auto'}])).id,saved.id);
});
test('expired worker leases get a new attempt and reject late results',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc);await rpc(db,'review_enqueue',['financial',a.id,'user']);
 const old=await rpc(db,'review_claim',['verification']);await db.exec("update review_jobs set lease_until=now()-interval '1 minute'");
 const current=await rpc(db,'review_claim',['verification']);assert.equal(current.attempt_seq,2);assert.notEqual(current.lease_token,old.lease_token);
 assert.equal((await rpc(db,'review_complete',[old.job_id,1,old.lease_token,{status:'failed',reason:'late'}])).stale,true);
 assert.equal((await rpc(db,'review_item',['financial',a.id])).verification_status,'candidate');
});
test('provider observations do not compare against manual adopted values',async t=>{
 const db=await setup(t),aDoc=await document(db,'eastmoney','2024-12-31','2025-02-01'),bDoc=await document(db,'formal','2024-12-31','2025-03-01');
 const a=await fact(db,aDoc);const [adopted]=await rpc(db,'review_command',['confirm',{items:[edit(a,bDoc,{mode:'custom',value:'110'})]},'user']);
 const payload={source_document_id:aDoc,observation_hash:'A-v1',fact:{report_period:'2024-12-31',metric_code:'net_profit',value:'100',unit:'CNY',period_type:'annual'}};
 assert.equal((await rpc(db,'review_observe',['financial',sid,payload])).changed,false);
 assert.equal((await rpc(db,'review_observe',['financial',sid,payload])).changed,false);
 assert.equal((await rpc(db,'review_item',['financial',adopted.id])).value,'110');
 const newer=await document(db,'eastmoney','2024-12-31','2025-04-01');
 const changed=await rpc(db,'review_observe',['financial',sid,{...payload,source_document_id:newer,observation_hash:'A-v2',fact:{...payload.fact,value:'120'}}]);
 assert(changed.changed);const item=await rpc(db,'review_item',['financial',changed.id]);assert.equal(item.value,'120');assert.equal(item.status,'changed');
 const summary=await rpc(db,'review_overview');assert(summary.pending_publish>0);assert(summary.invalidations.length>0);
});
test('metadata repair creates a new candidate and rejects stale or conflicting identities',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc,'net_profit','2024-09-30');
 const result=await rpc(db,'review_command',['metadata',{items:[edit(a,doc,{changes:{report_period:'2024-12-31',value:'101',currency:'CNY',unit:'CNY',period_type:'annual'}})]},'user']);
 assert.equal(result[0].verification_status,'candidate');assert.equal(result[0].report_period,'2024-12-31');assert.equal(result[0].review_data.ready,undefined);
 await assert.rejects(rpc(db,'review_item',['financial',a.id]),/not_current/);
});
test('missing metadata cannot be approved, including null units',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc);
 await db.query('update financial_facts set unit=null where financial_fact_id=$1',[a.id]);const broken=await rpc(db,'review_item',['financial',a.id]);
 await assert.rejects(rpc(db,'review_command',['confirm',{items:[edit(broken,doc)]},'user']),/metadata_incomplete/);
});
test('annual zero requires full evidence; waiting years cannot be completed early',async t=>{
 const db=await setup(t),doc=await document(db);await fact(db,doc);
 let years=await rpc(db,'review_annual_records');const y=years.find(y=>y.fiscal_year===2024);assert(y.ready);assert.equal(y.positive_plans,0);
 const payload={security_id:sid,fiscal_year:2024,version:0,evidence_id:doc,scope_checked:true,conclusion:'zero',note:'年报及全年公告明确无现金分红'};
 await assert.rejects(rpc(db,'review_command',['annual',payload,'user']),/zero_evidence/);
 await rpc(db,'review_command',['annual',{...payload,zero_confirmed:true},'user']);
 years=await rpc(db,'review_annual_records');assert.equal(years.find(y=>y.fiscal_year===2024).status,'complete');
 await rpc(db,'review_touch_year',[sid,2024,'source','新增分红事项']);
 years=await rpc(db,'review_annual_records');assert.equal(years.find(y=>y.fiscal_year===2024).status,'pending');
 const future=await document(db,'eastmoney','2099-12-31','2099-12-31');await fact(db,future,'revenue','2099-12-31');
 assert.equal((await rpc(db,'review_annual_records')).find(y=>y.fiscal_year===2099).ready,false);
});
test('invalidation remains visible until publication, even after its replacement is approved',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc);
 const [candidate]=await rpc(db,'review_command',['metadata',{items:[edit(a,doc,{changes:{report_period:a.report_period,value:'101',currency:'CNY',unit:'CNY',period_type:'annual'}})]},'user']);
 await rpc(db,'review_command',['confirm',{items:[edit(candidate,doc)]},'user']);
 const summary=await rpc(db,'review_overview');assert.equal(summary.invalidations.length,1);assert.equal(summary.pending_publish,1);
});
test('cloud publish is deduplicated; a failed dispatch can be retried with a new attempt',async t=>{
 const db=await setup(t);const first=await rpc(db,'review_command',['publish',{},'user']);
 assert.equal((await rpc(db,'review_command',['publish',{},'user'])).attempt_seq,1);
 await rpc(db,'review_publish_dispatched',[first.job_id,1,'not configured']);
 const retry=await rpc(db,'review_command',['publish',{},'user']);assert.equal(retry.attempt_seq,2);
 await rpc(db,'review_publish_dispatched',[first.job_id,1,'late error']);
 assert.equal((await db.query('select state from review_jobs')).rows[0].state,'queued');
});
test('database review functions are not directly executable by browser roles',async t=>{
 const db=await setup(t);await db.exec('set role authenticated');
 await assert.rejects(rpc(db,'review_command',['publish',{},'user']),/permission denied/);
 await db.exec('reset role');
});

test('adoption using the same provider evidence still compares future sync against the original observation',async t=>{
 const db=await setup(t),doc=await document(db,'eastmoney'),a=await fact(db,doc);
 const [manual]=await rpc(db,'review_command',['confirm',{items:[edit(a,doc,{mode:'custom',value:'110'})]},'user']);
 const incoming={source_document_id:doc,observation_hash:'original-observation',fact:{report_period:a.report_period,metric_code:'net_profit',value:'100',unit:'CNY',period_type:'annual'}};
 assert.equal((await rpc(db,'review_observe',['financial',sid,incoming])).changed,false);
 assert.equal((await rpc(db,'review_item',['financial',manual.id])).value,'110');
});
test('original display units are normalized server-side without losing the recorded source input',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc);
 const [saved]=await rpc(db,'review_command',['metadata',{items:[edit(a,doc,{changes:{report_period:a.report_period,value:'1.23456789',unit:'亿元',currency:'CNY',period_type:'annual'}})]},'user']);
 assert.equal(saved.value,'123456789.00000000');assert.equal(saved.unit,'CNY');
 assert.equal(saved.review_data.original_input.value,'1.23456789');assert.equal(saved.review_data.original_input.unit,'亿元');
});
test('recheck clears the batch-ready mark and cannot adopt a mismatched reference',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc);
 await db.query('update financial_facts set review_data=$2 where financial_fact_id=$1',[a.id,JSON.stringify({reference:{value:'5',unit:'USD',report_period:a.report_period}})]);
 await assert.rejects(rpc(db,'review_command',['confirm',{items:[edit(a,doc,{mode:'reference'})]},'user']),/not_comparable/);
 await rpc(db,'review_command',['mark_ready',{items:[edit(a,doc)]},'user']);await rpc(db,'review_enqueue',['financial',a.id,'user']);
 assert.equal((await rpc(db,'review_item',['financial',a.id])).review_data.ready,undefined);
});
test('cloud dispatch is claimed once and a new change during publication stays pending',async t=>{
 const db=await setup(t),doc=await document(db),a=await fact(db,doc);
 await rpc(db,'review_command',['confirm',{items:[edit(a,doc)]},'user']);
 const first=await rpc(db,'review_command',['publish',{},'user']);
 assert.equal(await rpc(db,'review_prepare_dispatch',[first.job_id,first.attempt_seq]),true);
 assert.equal(await rpc(db,'review_prepare_dispatch',[first.job_id,first.attempt_seq]),false);
 const active=await rpc(db,'review_claim',['publish',first.job_id,first.attempt_seq]);
 await rpc(db,'review_event',['change','another',sid,'user',{invalidate:false}]);
 const reused=await rpc(db,'review_command',['publish',{},'user']);assert.equal(reused.target_revision,first.target_revision);
 assert.equal(reused.lease_token,active.lease_token);
 const version=(await db.query("insert into dashboard_versions(is_complete,payload,calculation_version,content_hash) values(true,$1,'test','review-test') returning dashboard_version_id",[JSON.stringify({review_revision:first.target_revision})])).rows[0].dashboard_version_id;
 await rpc(db,'review_complete',[active.job_id,active.attempt_seq,active.lease_token,{status:'succeeded',version}]);
 assert.equal((await rpc(db,'review_overview',[version])).pending_publish,1);
});

test('new provider dividend candidates are current and audit-only changes do not dirty publication input',async t=>{
 const db=await setup(t),doc=await document(db,'eastmoney');
 const observed=await rpc(db,'review_observe',['dividend',sid,{source_document_id:doc,observation_hash:'new-plan',fact:{report_period:'2024-12-31',fiscal_year:2024,event_stage:'implemented',announcement_id:'event',value:'1',ex_date:'2025-06-01',bonus_shares_per_share:'0',transfer_shares_per_share:'0'}}]);
 const item=await rpc(db,'review_item',['dividend',observed.id]);assert.equal(item.verification_status,'candidate');
 const before=(await db.query('select input_revision from dashboard_publication_state')).rows[0].input_revision;
 await db.query("update dividend_events set review_data=review_data||'{\"reason\":\"等待本地执行器\"}' where dividend_event_id=$1",[observed.id]);
 assert.equal((await db.query('select input_revision from dashboard_publication_state')).rows[0].input_revision,before);
 assert.equal((await rpc(db,'review_history',['dividend',observed.id])).filter(e=>e.event_type==='observation').length,1);
});

test('the first sync after repairing a legacy report period does not recreate the rejected old period',async t=>{
 const db=await setup(t),raw=await document(db,'eastmoney','2024-09-30'),formal=await document(db),a=await fact(db,raw,'net_profit','2024-09-30');
 const [corrected]=await rpc(db,'review_command',['metadata',{items:[edit(a,formal,{changes:{report_period:'2024-12-31',value:'110',currency:'CNY',unit:'CNY',period_type:'annual'}})]},'user']);
 const incoming={source_document_id:raw,observation_hash:'legacy-original',fact:{report_period:a.report_period,metric_code:'net_profit',value:'100',unit:'CNY',period_type:'year_to_date'}};
 assert.equal((await rpc(db,'review_observe',['financial',sid,incoming])).changed,false);
 assert.equal((await db.query('select count(*)::int n from financial_facts where is_current')).rows[0].n,1);
 assert.equal((await rpc(db,'review_item',['financial',corrected.id])).value,'110');
});
test('dividend stages link only through an explicit stable plan and invalidate annual completeness',async t=>{
 const db=await setup(t),doc=await document(db);
 const create=async(plan,stage,announcement)=>{const id=(await db.query(`insert into dividend_events(security_id,fiscal_year,report_period,event_stage,announcement_id,plan_key,cash_dividend_per_share,cash_dividend_total,ex_date,bonus_shares_per_share,transfer_shares_per_share,source_document_id,is_current)
 values($1,2024,'2024-12-31',$2,$3,$4,1,100,'2025-06-01',0,0,$5,true) returning dividend_event_id`,[sid,stage,announcement,plan,doc])).rows[0].dividend_event_id;return rpc(db,'review_item',['dividend',id]);};
 const old=await create('final-2024','approved','proposal'),next=await create(null,'implemented','implementation');
 const correction={kind:'dividend',id:next.id,security_id:sid,fact_hash:next.fact_hash,evidence_id:doc,note:'同一方案已实施',reviewed:true,changes:{value:'1',report_period:'2024-12-31',unit:'CNY/share',currency:'CNY',period_type:'event',plan_key:'final-2024',event_stage:'implemented',cash_dividend_total:'100',bonus_shares_per_share:'0',transfer_shares_per_share:'0',ex_date:'2025-06-01'}};
 await assert.rejects(rpc(db,'review_command',['metadata',{items:[correction]},'user']),/plan_link_required/);
 correction.changes.supersedes_id=old.id;
 const [linked]=await rpc(db,'review_command',['metadata',{items:[correction]},'user']);assert.equal(linked.fields.plan_key,'final-2024');assert.equal(linked.version,2);
 assert.equal((await db.query('select count(*)::int n from dividend_events where is_current')).rows[0].n,1);
 assert((await rpc(db,'review_overview')).invalidations.some(x=>x.kind==='annual'));
});
