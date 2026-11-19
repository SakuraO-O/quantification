import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {PGlite} from '@electric-sql/pglite';

const migrations = fileURLToPath(new URL('../supabase/migrations/', import.meta.url));
async function database(t) {
  const db = new PGlite();
  t.after(() => db.close());
  await db.exec('create role anon; create role authenticated; create role service_role bypassrls; create schema auth; create table auth.users(id uuid primary key);');
  for (const name of readdirSync(migrations).filter(name=>name.endsWith('.sql')).sort()) {
    if (name.endsWith('_dashboard_publication_checks.sql')) {
      await db.exec("insert into notification_dispatches(message_type,status,dispatch_key,sent_at) values('morning_report','succeeded','morning_report:legacy-version','2026-11-10T00:30:00Z')");
    }
    // PGlite has the built-in UUID generator; the native extension is unavailable.
    await db.exec(readFileSync(migrations+name,'utf8').replace('create extension if not exists pgcrypto;',''));
  }
  return db;
}
async function batch(db, tasks = {}) {
  const result = await db.query("insert into dashboard_batches(phase,tasks) values('manual',$1) returning batch_id", [JSON.stringify(tasks)]);
  return result.rows[0].batch_id;
}
async function begin(db, id) {
  return (await db.query('select begin_dashboard_publication($1) as result',[id])).rows[0].result;
}
async function finish(db, id, hash) {
  return (await db.query("select finish_dashboard_publication($1,'{}',true,'{}','test',$2) as result",[id,hash])).rows[0].result;
}
async function fail(db, id) {
  await db.query("select fail_dashboard_publication($1,'test failure')",[id]);
}

test('input writes and upstream state changes fence out inconsistent publications', async t => {
  const db=await database(t);
  const id=await batch(db,{'sync-market:CN':'succeeded'});
  let check=await begin(db,id);
  await db.exec("insert into market_calendars(market,trade_date,is_trading_day,source) values('CN','2026-11-18',true,'test')");
  await assert.rejects(finish(db,check.check_id,'mixed'), /inputs changed/);
  await fail(db,check.check_id);
  check=await begin(db,id);
  await db.query("select record_dashboard_batch_task($1,'sync-market:CN','failed')",[id]);
  await assert.rejects(finish(db,check.check_id,'changed-task'), /upstream batch changed/);
  await fail(db,check.check_id);
  await assert.rejects(begin(db,id), /unfinished or fatal/);
});

test('publication lease excludes concurrent and expired workers', async t => {
  const db=await database(t);
  const id=await batch(db);
  const old=await begin(db,id);
  await assert.rejects(begin(db,id),/already running/);
  await db.exec("update dashboard_publication_state set lease_until=now()-interval '1 second'");
  const replacement=await begin(db,id);
  await assert.rejects(finish(db,old.check_id,'expired'),/lease lost/);
  await fail(db,old.check_id);
  await assert.rejects(begin(db,id),/already running/);
  await finish(db,replacement.check_id,'new-worker');
});

test('reused content stays immutable and can become the current version again', async t => {
  const db=await database(t);
  const id=await batch(db);
  let check=await begin(db,id);
  const first=await finish(db,check.check_id,'first');
  check=await begin(db,id);
  const same=await finish(db,check.check_id,'first');
  assert.equal(first.dashboard_version_id,same.dashboard_version_id);
  assert.equal(first.generated_at,same.generated_at);
  check=await begin(db,id);
  await finish(db,check.check_id,'second');
  check=await begin(db,id);
  await finish(db,check.check_id,'first');
  const state=(await db.query('select dashboard_runtime_status() as result')).rows[0].result;
  assert.equal(state.current_version_id,first.dashboard_version_id);
});

test('dispatch migration preserves sent history and date claims are atomic', async t => {
  const db=await database(t);
  const legacy=await db.query("select dispatch_key from notification_dispatches where dispatch_key='morning_report:2026-11-10'");
  assert.equal(legacy.rows.length,1);
  const claim=async key=>(await db.query("select claim_dashboard_dispatch($1,null,'morning_report') as claimed",[key])).rows[0].claimed;
  assert.equal(await claim('morning_report:2026-11-10'),false);
  assert.equal(await claim('morning_report:2026-11-19'),true);
  assert.equal(await claim('morning_report:2026-11-19'),false);
  assert.equal(await claim('morning_report:2026-11-20'),true);
});

test('anonymous clients cannot call publication or operational status functions', async t => {
  const db=await database(t);
  await db.exec('set role anon');
  await assert.rejects(db.query('select dashboard_runtime_status()'),/permission denied/);
  await assert.rejects(db.query("select claim_dashboard_dispatch('x',null,'morning_report')"),/permission denied/);
});
