import test from 'node:test';
import assert from 'node:assert/strict';
import {fetchCompanySnapshot,overlaySavedRows} from '../companies/sync.js';
import {loadCompanyParts} from '../companies/sync.js';
import {companyParts} from '../company-parts.mjs';
import {createDatabaseLoader} from '../companies/sync.js';

test('Supabase uses a publishable key, shows 50 first, pages concurrently and caches the revision',async()=>{
 const records=Array.from({length:1051},(_,i)=>({id:String(i).padStart(5,'0'),name:'Company'}));
 let downloads=0;const progress=[];
 const load=createDatabaseLoader({url:'https://example.supabase.co',key:'publishable'},async(url,opts)=>{
  assert.equal(opts.headers.apikey,'publishable');assert.equal(opts.credentials,'omit');
  const parsed=new URL(url);let value;
  if(parsed.pathname.endsWith('/directory_meta'))value=[{status:'ready',revision:'one',total:records.length}];
  else{
   downloads++;const offset=Number(parsed.searchParams.get('offset')||0);
   const rows=records.slice(offset);
   value=rows.slice(0,Number(parsed.searchParams.get('limit'))).map(record=>({id:record.id,record}));
  }
  return {ok:true,json:async()=>value};
 });
 const result=await load((data)=>progress.push(data.loaded));
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
 await assert.rejects(load(),/არასრულად|ფორმატი/);
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


test('compressed full snapshot verifies checksum, retains evidence and reuses persistent cache',async()=>{
 const {gzipSync}=await import('node:zlib');const {createHash}=await import('node:crypto');
 const data={version:1,records:[{id:'a',name:'ქართული კომპანია',evidence:[{value:'verified'}]}]};
 const bytes=gzipSync(JSON.stringify(data)),sha256=createHash('sha256').update(bytes).digest('hex');
 const meta={status:'ready',revision:'snapshot-revision',total:1,snapshot:{path:`snapshots/${sha256}.json.gz`,sha256,bytes:bytes.length,total:1}};
 let downloads=0,saved,cached=0;
 const cache={read:async()=>saved,write:async value=>{saved=value;}};
 const fetcher=async url=>{if(url.includes('directory_meta'))return Response.json([meta]);downloads++;assert.ok(url.includes('/object/public/company-directory/'));return new Response(bytes);};
 const first=createDatabaseLoader({url:'https://example.supabase.co',key:'public'},fetcher,{cache});
 assert.deepEqual((await first()).records,data.records);assert.equal(downloads,1);
 const second=createDatabaseLoader({url:'https://example.supabase.co',key:'public'},fetcher,{cache});
 assert.deepEqual((await second(null,()=>{cached++;})).records,data.records);
 assert.equal(cached,1);assert.equal(downloads,1);
 saved.bytes=new Uint8Array(bytes.length);
 const third=createDatabaseLoader({url:'https://example.supabase.co',key:'public'},fetcher,{cache});
 await third();assert.equal(downloads,2);
});

test('corrupt compressed downloads cannot be cached or displayed as a completed snapshot',async()=>{
 const {gzipSync}=await import('node:zlib');const {createHash}=await import('node:crypto');
 const bytes=gzipSync(JSON.stringify({version:1,records:[{id:'a'}]})),sha256=createHash('sha256').update(bytes).digest('hex');
 const meta={status:'ready',revision:'r',total:1,snapshot:{path:`snapshots/${sha256}.json.gz`,sha256,bytes:bytes.length,total:1}};
 let saved=false;
 const load=createDatabaseLoader({url:'https://example.supabase.co',key:'public'},async url=>url.includes('directory_meta')?Response.json([meta]):new Response(new Uint8Array(bytes.length)),{cache:{write:async()=>{saved=true;}}});
 await assert.rejects(load(),/დაზიანებულია/);assert.equal(saved,false);
});

test('parallel REST fallback remains ordered and never exceeds four concurrent pages',async()=>{
 const records=Array.from({length:5055},(_,i)=>({id:String(i).padStart(5,'0')}));
 let running=0,maximum=0;
 const load=createDatabaseLoader({url:'https://example.supabase.co',key:'public'},async url=>{
  const params=new URL(url).searchParams;
  if(url.includes('directory_meta'))return Response.json([{status:'ready',revision:'r',total:records.length}]);
  running++;maximum=Math.max(maximum,running);
  const offset=Number(params.get('offset')||0);
  await new Promise(resolve=>setTimeout(resolve,offset===50?20:1));running--;
  return Response.json(records.slice(offset,offset+Number(params.get('limit'))).map(record=>({id:record.id,record})));
 });
 assert.deepEqual((await load()).records,records);assert.equal(maximum,4);
});
