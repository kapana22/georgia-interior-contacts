import test from 'node:test';
import assert from 'node:assert/strict';
import {fetchCompanySnapshot,overlaySavedRows} from '../companies/sync.js';

test('refresh adds remote companies and preserves local edits and evidence',()=>{
  const result=overlaySavedRows([{id:'a',name:'Remote',phone:'123',evidence:['new']},{id:'b',name:'New company'}],[{id:'a',name:'Edited',evidence:['old']},{id:'local',name:'Local company'}]);
  assert.equal(result.length,3);
  assert.equal(result[0].name,'Edited');
  assert.equal(result[0].phone,'123');
  assert.deepEqual(result[0].evidence,['new','old']);
});
test('refresh bypasses browser cache and rejects invalid snapshots',async()=>{
  await fetchCompanySnapshot('https://example.test',async(url,options)=>{
    assert.equal(options.cache,'no-store');
    return {ok:true,json:async()=>({version:1,records:[{id:'a'}]})};
  });
  await assert.rejects(fetchCompanySnapshot('test',async()=>({ok:true,json:async()=>({version:1,records:[]})})));
  await assert.rejects(fetchCompanySnapshot('test',async()=>({ok:false})));
});

test('live loader uses immutable commits and downloads only changed snapshots',async()=>{
 const {createLiveLoader}=await import('../companies/sync.js');
 let sha='a'.repeat(40),downloads=0;
 const load=createLiveLoader(async(url)=>{
  if(url.includes('api.github.com'))return {ok:true,json:async()=>({object:{sha}})};
  downloads++;assert.ok(url.includes('/'+sha+'/data.json'));
  return {ok:true,json:async()=>({version:1,records:[{id:sha}]})};
 });
 assert.equal((await load()).records[0].id,sha);await load();assert.equal(downloads,1);
 sha='b'.repeat(40);assert.equal((await load()).records[0].id,sha);assert.equal(downloads,2);
});
