import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,readdir,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {readCompanySnapshot,writeCompanySnapshot} from '../../scripts/company_snapshot.mjs';

async function temporary(t) {
  const dir=await mkdtemp(path.join(os.tmpdir(),'company-snapshot-'));
  t.after(()=>rm(dir,{recursive:true,force:true}));
  return {dir,file:path.join(dir,'data.json')};
}

test('streamed snapshots preserve Unicode, nested evidence, metadata and empty arrays across byte boundaries',async t=>{
  const {file}=await temporary(t);
  const original={version:1,records:[{id:'a',name:'შპს „კომპანია“ 🏢',tax_id:'001234567',
    evidence:[{value:'quotes " \\ \n } ] ,',source_url:'https://example.ge'}],
    source_records:[{details:{numbers:[1,-2,1.2e20],empty:[],nullable:null,active:true}}]}],
    updated_at:'2026-11-04',quality:{nested:[{note:'საქსტატი'}]}};
  await writeCompanySnapshot(file,original);
  assert.deepEqual(JSON.parse(await readFile(file,'utf8')),original);
  assert.deepEqual(await readCompanySnapshot(file,{highWaterMark:1}),original);
  await writeCompanySnapshot(file,{before:0,records:[],after:false});
  assert.deepEqual(await readCompanySnapshot(file),{before:0,records:[],after:false});
});

test('malformed or truncated snapshots fail instead of publishing partial records',async t=>{
  const {file}=await temporary(t);
  for(const text of ['{"records":[{"id":"a"}', '{"records":[],"meta":', '{"records":{}}', '[]']) {
    await writeFile(file,text);
    await assert.rejects(readCompanySnapshot(file,{highWaterMark:2}));
  }
});

test('failed streaming writes retain the original snapshot and remove incomplete temporary files',async t=>{
  const {dir,file}=await temporary(t);
  const original={records:[{id:'keep'}],version:1};
  await writeCompanySnapshot(file,original);
  const circular={id:'broken'};circular.self=circular;
  await assert.rejects(writeCompanySnapshot(file,{records:[{id:'partial'},circular]}),/circular/i);
  assert.deepEqual(await readCompanySnapshot(file),original);
  assert.deepEqual(await readdir(dir),['data.json']);
});
