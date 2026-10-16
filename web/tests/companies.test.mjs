import test from 'node:test';
import assert from 'node:assert/strict';
import {classify,cityKey,filterRecords,normalizeImport,mergeRecords,csv} from '../companies/core.js';

test('development suggestions are explicitly unconfirmed and software is separate',()=>{
  assert.equal(classify({name:'ABC დეველოპმენტი'}).category,'development');
  assert.equal(classify({name:'ABC Development'}).status,'candidate');
  assert.equal(classify({name:'Software Development'}).category,'technology');
  assert.equal(classify({name:'ABC'}).category,'unknown');
  assert.equal(classify({name:'ABC',category:'development',classification_status:'confirmed'}).status,'confirmed');
});
test('audience filters combine category, contact, city, status and search',()=>{
  const rows=[{name:'ABC Development',phone:'123',city:'თბილისი'},{name:'ABC',category:'development',classification_status:'confirmed',city:'ბათუმი',email:'a@b.ge'}];
  assert.equal(filterRecords(rows,{category:'development'}).length,2);
  assert.equal(filterRecords(rows,{category:'development',contact:'phone',city:'თბილისი',query:'abc'}).length,1);
  assert.equal(filterRecords(rows,{status:'confirmed',contact:'phone'}).length,0);
});
test('scraper import retains source facts; repeat import does not duplicate companies',()=>{
  const incoming=normalizeImport([{id:'scrape:1',name:'Company',tax_id:'001',facts:[{kind:'phone',value:'+995555123456',source_url:'https://example.org'}]}]);
  assert.equal(incoming[0].phone,'+995555123456');
  const base=[{id:'export:1',name:'Company',tax_id:'001',email:'old@example.org',source:'original.xlsx'}];
  const merged=mergeRecords(base,incoming);
  assert.equal(merged.length,1);assert.equal(merged[0].email,'old@example.org');
  assert.equal(merged[0].id,'export:1');assert.equal(merged[0].evidence.length,1);
  assert.equal(mergeRecords(merged,incoming).length,1);
  assert.equal(base[0].phone,undefined);
});
test('ambiguous tax IDs and conflicting identities fail instead of merging unrelated companies',()=>{
  assert.throws(()=>mergeRecords([{id:'a',tax_id:'1'},{id:'b',tax_id:'1'}],[{id:'c',tax_id:'1'}]));
  assert.throws(()=>mergeRecords([{id:'a',tax_id:'1'}],[{id:'a',tax_id:'2'}]));
  assert.throws(()=>normalizeImport([{name:'X',category:'development',classification_status:'confirmed'}]));
});
test('CSV neutralizes spreadsheet formulas and preserves leading zero identifiers',()=>{
  const out=csv([{name:'=HYPERLINK("x")',tax_id:'001',phone:'+995555123456'}]);
  assert.ok(out.includes("'=HYPERLINK"));assert.ok(out.includes('"001"'));assert.ok(out.includes("'+995"));
});
test('city filtering groups spelling variants without mistaking a street for a city',()=>{
  assert.equal(cityKey('საქართველო, ქ. თბილისის, საბურთალო'),'თბილისი');
  assert.equal(cityKey('ქ.თბილისი'),'თბილისი');
  assert.equal(cityKey('ქუთაისი/თბილისი'),'ქუთაისი');
  assert.equal(cityKey('ჭიათურა, თბილისის ქ. II შესახვევი #6'),'ჭიათურა');
  assert.equal(filterRecords([{city:'ქ. თბილისი'}],{city:'თბილისი'}).length,1);
});
test('imports require stable identity and backups retain provenance',()=>{
  assert.throws(()=>normalizeImport([{name:'Unidentified'}]));
  const [r]=normalizeImport([{id:'x',name:'X',source_row:5,history:[{source:'old'}],evidence:[{kind:'phone',value:'123'}]}]);
  assert.equal(r.source_row,5);assert.equal(r.history[0].source,'old');assert.equal(r.evidence.length,1);
  const twice=mergeRecords(mergeRecords([], [r]),[r]);
  assert.equal(mergeRecords(twice,[r])[0].history.length,twice[0].history.length);
});
