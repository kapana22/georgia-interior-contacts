import test from 'node:test';
import assert from 'node:assert/strict';
import {createCollector} from '../api/collector.mjs';
async function call(handler, method='GET', body, headers={}) {
  let text; const res={setHeader(){},end(value){text=value;}};
  await handler({method,body,headers:{host:'example.test','content-type':'application/json',...headers}},res);
  return {status:res.statusCode,body:JSON.parse(text)};
}
test('public state reveals no token and caches authenticated upstream calls',async()=>{
  const calls=[]; const handler=createCollector({token:()=> 'SECRET',fetcher:async(url,options)=>{calls.push({url,options});return Response.json({workflow_runs:[{id:12,status:'in_progress',token:'SECRET'}]});}});
  const a=await call(handler),b=await call(handler);
  assert.equal(a.status,200); assert.equal(b.body.runs[0].id,12); assert.equal(calls.length,1);
  assert.equal(calls[0].options.headers.Authorization,'Bearer SECRET'); assert.ok(!JSON.stringify(a).includes('SECRET'));
});
test('public dispatch uses fixed workflow and reuses active jobs',async()=>{
  let posts=0; const handler=createCollector({token:()=> 'SECRET',fetcher:async(_,options)=>{if(options.method==='POST')posts++;return Response.json({workflow_runs:[{id:12,status:'queued'}]});}});
  const result=await call(handler,'POST',{limit:1000,sources:'config/sources.json'});
  assert.equal(result.body.workflow_run_id,12);assert.equal(posts,0);
});
test('dispatch validates source, origin and limit; does not expose arbitrary GitHub writes',async()=>{
  const calls=[]; const handler=createCollector({token:()=> 'SECRET',fetcher:async(url,options)=>{calls.push({url,options});return Response.json(options.method==='POST'?{workflow_run_id:99}:{workflow_runs:[]});}});
  assert.equal((await call(handler,'POST',{limit:1001,sources:'config/sources.json'})).status,400);
  assert.equal((await call(handler,'POST',{limit:3,sources:'config/evil.json'})).status,400);
  assert.equal((await call(handler,'POST',{limit:3,sources:'config/smoke.json'},{origin:'https://other.test'})).status,403);
  const result=await call(handler,'POST',{limit:3,sources:'config/smoke.json'});
  assert.equal(result.body.workflow_run_id,99);
  assert.deepEqual(JSON.parse(calls[1].options.body),{ref:'main',inputs:{limit:'3',sources:'config/smoke.json'}});
  assert.equal((await call(handler,'POST',{limit:3,sources:'config/smoke.json'})).status,429);
});
test('missing server credential permits public page but cannot dispatch',async()=>{
  const handler=createCollector({token:()=> ''}); assert.equal((await call(handler)).body.configured,false);
  assert.equal((await call(handler,'POST',{limit:3,sources:'config/smoke.json'})).status,503);
});
test('failed dispatch is not automatically retried',async()=>{
  let posts=0;const handler=createCollector({token:()=> 'SECRET',fetcher:async(_,options)=>{if(options.method==='POST'){posts++;throw Error('timeout');}return Response.json({workflow_runs:[]});}});
  assert.equal((await call(handler,'POST',{limit:3,sources:'config/smoke.json'})).status,503);assert.equal(posts,1);
});
