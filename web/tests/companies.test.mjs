import test from 'node:test';
import assert from 'node:assert/strict';
import {classify,cityKey,filterRecords,normalizeImport,mergeRecords,csv,exportRows,categoryIds,publicLinks} from '../companies/core.js';
import {integrate} from '../../scripts/merge_companies.mjs';

test('development suggestions are explicitly unconfirmed and software is separate',()=>{
  assert.equal(classify({name:'ABC დეველოპმენტი'}).category,'development');
  assert.equal(classify({name:'ABC Development'}).status,'candidate');
  assert.equal(classify({name:'Software Development'}).category,'technology');
  assert.equal(classify({name:'ABC'}).category,'unknown');
  assert.equal(classify({name:'ABC',category:'development',classification_status:'confirmed'}).status,'confirmed');
});
test('high-confidence business keywords classify as unconfirmed suggestions',()=>{
  for(const [name,category] of [['ვგ ქონსთრაქშენ','construction'],['ამბიენს არქიტექტს','architecture'],['ავტოსერვისი 2022','auto'],['სილქესთეტიკი','beauty'],['ევროპული საცხობი','food']]){
    assert.equal(classify({name}).category,category,name);
    assert.equal(classify({name}).status,'candidate',name);
  }
  assert.equal(classify({name:'ამირბარი და მეტი'}).category,'unknown');
  assert.equal(classify({name:'ფარდის სალონი'}).category,'unknown');
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
test('export uses exactly combined filters, selection remains explicit',()=>{
  const rows=[{id:'a',name:'A',category:'construction',city:'თბილისი',phone:'123',legal_status:'შპს'},{id:'b',name:'B',category:'construction',city:'ბათუმი',phone:'123'},{id:'c',name:'C',category:'development',city:'თბილისი'}];
  const filtered=filterRecords(rows,{category:'construction',city:'თბილისი',contact:'phone',legal:'შპს'});
  assert.deepEqual(exportRows(rows,filtered,new Set(['b']),'filtered').map(r=>r.id),['a']);
  assert.deepEqual(exportRows(rows,filtered,new Set(['b']),'selected').map(r=>r.id),['b']);
  assert.equal(exportRows(rows,filtered,new Set(),'all').length,3);
  const out=csv(filtered,{compact:true});assert.ok(out.includes('"A"'));assert.ok(!out.includes('"B"'));assert.ok(!out.includes('შემოწმება'));assert.ok(!out.includes('წყარო'));
});
test('unified audiences and companies with multiple directions filter together',()=>{
  const r={name:'A',category:'development',industries:['construction'],audiences:['interior'],phone:'123'};
  assert.deepEqual(categoryIds(r),['development','construction']);
  assert.equal(filterRecords([r],{category:'construction',audience:'interior'}).length,1);
  assert.equal(filterRecords([r],{audience:'website'}).length,1);
  assert.equal(filterRecords([{...r,website:'https://example.org'}],{audience:'website'}).length,0);
});
test('growth joins matching name and contact, retains legal data, repeat imports add nothing',()=>{
  const base=[{id:'excel:1',name:'შპს კომპანია',tax_id:'001',legal_status:'შპს',phone:'+995555123456'}];
  const incoming=[{id:'yell:1',name:'კომპანია',phone:'555123456',category:'construction',email:'new@example.org'}];
  const first=integrate(base,incoming);assert.equal(first.added,0);assert.equal(first.updated,1);assert.equal(first.records[0].tax_id,'001');
  assert.equal(integrate(first.records,incoming).added,0);
  assert.equal(integrate(base,[{...incoming[0],name:'სხვა კომპანია'}]).added,1);
});
test('social links and original scraper metadata survive imports and exports',()=>{
 const original={id:'social:1',name:'A',website:'facebook.com/example',facts:[{kind:'instagram',value:'https://instagram.com/example',source_url:'https://example.org'}],checks:[{outcome:'listed'}]};
 const [r]=normalizeImport([original]);assert.equal(r.instagram,'https://instagram.com/example');assert.deepEqual(r.source_records[0].checks,original.checks);
 assert.deepEqual(publicLinks(r).map(l=>l.kind).sort(),['facebook','instagram']);
 assert.equal(publicLinks({website:'javascript:alert(1)'}).length,0);
 assert.ok(csv([r]).includes('https://instagram.com/example'));
 const merged=mergeRecords([r],normalizeImport([{id:'social:1',name:'A',phone:'123'}]));assert.equal(merged[0].source_records.length,2);assert.equal(merged[0].instagram,r.instagram);
});
test('all filters support multiple values: OR within a filter, AND across filters',()=>{
 const rows=[
  {id:'a',name:'A',category:'development',city:'თბილისი',phone:'123',legal_status:'შპს'},
  {id:'b',name:'B',category:'construction',city:'ბათუმი',email:'b@example.org',legal_status:'სს'},
  {id:'c',name:'C',category:'architecture',city:'თბილისი',phone:'123',legal_status:'შპს'},
  {id:'d',name:'D',category:'construction',city:'ქუთაისი',phone:'123',legal_status:'შპს'},
  {id:'e',name:'E',category:'construction',city:'ბათუმი',legal_status:'შპს'},
 ];
 const filters={category:['development','construction'],city:['თბილისი','ბათუმი'],contact:['phone','email'],legal:['შპს','სს']};
 const result=filterRecords(rows,filters);assert.deepEqual(result.map(r=>r.id),['a','b']);
 assert.equal(filterRecords(rows,{category:[],city:[],contact:[],legal:[],audience:[]}).length,5);
 assert.deepEqual(exportRows(rows,result,new Set(),'filtered').map(r=>r.id),['a','b']);
 assert.equal(filterRecords([{...rows[0],audiences:['interior'],website:'https://example.org'},rows[1]],{audience:['interior','website']}).length,2);
});
