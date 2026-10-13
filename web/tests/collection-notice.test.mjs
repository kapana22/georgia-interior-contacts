import test from 'node:test';
import assert from 'node:assert/strict';
import {collectionNotice} from '../core.js';
const now=Date.parse('2026-10-13T12:00:00Z');
const active={continuous:true,github_run_id:'1',collection_status:'active',work_remaining:true,checkpoint_at:'2026-10-13T11:55:00Z'};
test('active collection does not show an exhausted popup',()=>assert.equal(collectionNotice(active,now),null));
test('exhaustion, error and stalled states stay distinct',()=>{
 assert.equal(collectionNotice({...active,collection_status:'exhausted'},now).kind,'exhausted');
 assert.equal(collectionNotice({...active,collection_status:'paused',remaining_businesses:9},now).kind,'paused');
 assert.equal(collectionNotice({...active,status:'failed'},now).kind,'error');
});
test('Retry-After notice waits until the actual source pause expires',()=>{
 const report={...active,collection_status:'waiting',resume_after:now/1000+3600};
 assert.equal(collectionNotice(report,now).kind,'waiting');
 assert.equal(collectionNotice(report,now+3600*1000),null);
 assert.equal(collectionNotice(report,now+90*60*1000).kind,'stale');
});
test('stale data reports uncertainty rather than exhaustion',()=>{
 const report={...active,checkpoint_at:'2026-10-13T10:00:00Z'};
 assert.equal(collectionNotice(report,now).kind,'stale');
 assert.equal(collectionNotice({...report,work_remaining:false},now),null);
});
test('notice identity is stable during polling but changes for a new run',()=>{
 const r={...active,collection_status:'exhausted'};
 assert.equal(collectionNotice(r,now).key,collectionNotice(r,now+90000).key);
 assert.notEqual(collectionNotice(r,now).key,collectionNotice({...r,github_run_id:'2'},now).key);
});
