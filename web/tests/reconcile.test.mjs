import test from 'node:test';import assert from 'node:assert/strict';
import {reconcile} from '../../scripts/reconcile_companies.mjs';
import {overlaySavedRows} from '../companies/sync.js';

test('same tax and normalized name merge without losing contacts or source rows',()=>{
 const rows=[{id:'a',name:'შ.პ.ს. სტუდია',tax_id:'123456789',phone:'111',email:'one@a.ge',source:'import'},{id:'b',name:'სტუდია',tax_id:'123456789',phone:'222',email:'two@a.ge',source:'import'}];
 const r=reconcile(rows);assert.equal(r.merged,1);assert.equal(r.records.length,1);
 assert.equal(r.records[0].phone,'111; 222');assert.equal(r.records[0].email,'one@a.ge; two@a.ge');
 assert.deepEqual(r.records[0].merged_ids,['b']);assert.ok(r.records[0].source_records.some(x=>x.id==='b'));
 assert.equal(reconcile(r.records).merged,0);
 assert.deepEqual(reconcile(r.records).records,r.records);
 const over=overlaySavedRows(r.records,[{id:'b',name:'Local correction'}]);assert.equal(over.length,1);assert.equal(over[0].id,'a');assert.equal(over[0].name,'Local correction');
});
test('different names on same tax stay separate and are visibly flagged',()=>{
 const r=reconcile([{id:'a',name:'One',tax_id:'123456789'},{id:'b',name:'Two',tax_id:'123456789'}]);
 assert.equal(r.records.length,2);assert.equal(r.conflicting_records,2);assert.ok(r.records.every(x=>x.quality_notes.includes('გადასამოწმებელია')));
});
test('same names with different or missing tax IDs never merge',()=>{
 assert.equal(reconcile([{id:'a',name:'One'},{id:'b',name:'One'},{id:'c',name:'One',tax_id:'123456789'}]).merged,0);
});
