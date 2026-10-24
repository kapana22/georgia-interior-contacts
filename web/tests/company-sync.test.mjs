import test from 'node:test';
import assert from 'node:assert/strict';
import {fetchCompanySnapshot,overlaySavedRows} from '../companies/sync.js';
import {loadCompanyParts} from '../companies/sync.js';
import {companyParts} from '../company-parts.mjs';
import {createDatabaseLoader} from '../companies/sync.js';

test('Supabase uses a publishable key, shows 50 first, pages by ID and caches the revision',async()=>{
 const records=Array.from({length:1051},(_,i)=>({id:String(i).padStart(5,'0'),name:'Company'}));
 let downloads=0;const progress=[];
 const load=createDatabaseLoader({url:'https://example.supabase.co',key:'publishable'},async(url,opts)=>{
  assert.equal(opts.headers.apikey,'publishable');assert.equal(opts.credentials,'omit');
  const parsed=new URL(url);let value;
  if(parsed.pathname.endsWith('/directory_meta'))value=[{status:'ready',revision:'one',total:records.length}];
  else{
   downloads++;const after=parsed.searchParams.get('id')?.slice(3);
   const rows=after?records.filter(r=>r.id>after):records;
   value=rows.slice(0,Number(parsed.searchParams.get('limit'))).map(record=>({id:record.id,record}));
  }
  return {ok:true,json:async()=>value};
 });
 const result=await load((data)=>progress.push(data.records.length));
 assert.deepEqual(progress,[50,1050,1051]);assert.deepEqual(result.records,records);
 await load();assert.equal(downloads,3);
});

test('Supabase refuses mixed revisions and retries the whole snapshot after a publishing failure',async()=>{
 let metaCalls=0,stable=false;
 const load=createDatabaseLoader({url:'https://example.supabase.co',key:'public'},async(url)=>({ok:true,json:async()=>
  url.includes('/directory_meta?')?[{status:'ready',revision:stable?'stable':String(++metaCalls),total:1}]:[{id:'a',record:{id:'a'}}]
 }));
 await assert.rejects(load(),/შეიცვალა/);
 stable=true;assert.equal((await load()).records.length,1);
});

test('Supabase does not publish partial or duplicate directory results',async()=>{
 const load=createDatabaseLoader({url:'https://example.supabase.co',key:'public'},async(url)=>({ok:true,json:async()=>
  url.includes('/directory_meta?')?[{status:'ready',revision:'one',total:2}]:[{id:'a',record:{id:'a'}}]
 }));
 await assert.rejects(load(),/ფორმატი/);
});

test('progressive loading shows 50 records before requesting the rest, retaining all fields',async()=>{
 const data={version:1,records:Array.from({length:2101},(_,i)=>({id:String(i),evidence:[{value:'source '+i}]}))};
 const files=companyParts(data),progress=[];
 const result=await loadCompanyParts((part,total)=>{progress.push(part.records.length);assert.equal(total,2101);},async(url)=>{
  if(!url.endsWith('index.json'))assert.ok(progress.length>0);
  return {ok:true,json:async()=>JSON.parse(files.get(url.split('/').at(-1)))};
 });
 assert.deepEqual(progress,[50,1050,2050,2101]);
 assert.deepEqual(result.records,data.records);
});

test('a failed chunk preserves already displayed rows and rejects completion',async()=>{
 const files=companyParts({records:Array.from({length:51},(_,i)=>({id:String(i)}))});
 let shown=0;
 await assert.rejects(loadCompanyParts(data=>{shown=data.records.length;},async(url)=>url.endsWith('index.json')?{ok:true,json:async()=>JSON.parse(files.get('index.json'))}:{ok:false}));
 assert.equal(shown,50);
});

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
