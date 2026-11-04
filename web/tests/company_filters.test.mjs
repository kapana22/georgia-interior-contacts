import test from 'node:test';
import assert from 'node:assert/strict';
import {classify,categoryIds,normalizeImport} from '../companies/core.js';
import {evaluateFilters,registryMatched} from '../companies/filter-model.js';

test('official activity codes classify companies even when the company name says nothing about the sector',()=>{
  for(const [code,category] of [['41.20.0','construction'],['41.10.0','development'],['47.11.0','trade'],['68.20.2','real-estate'],['85.31.0','education'],['71.11.0','architecture'],['49.41.0','transport'],['74.10.0','design']]){
    const row={name:'შპს ალფა',category:'unknown',activity_code:code,activity_source_url:'https://br.geostat.ge/?identificationNumber=123456789'};
    assert.equal(classify(row).category,category);assert.equal(classify(row).status,'confirmed');
    assert.ok(!categoryIds(row).includes('unknown'));
  }
  assert.equal(classify({name:'შპს ალფა',activity_code:'00.00.0'}).category,'unknown');
  assert.deepEqual(categoryIds({name:'Alpha',category:'unknown',industries:['construction']}),['construction']);
});

test('facet counts apply search and all other selected filters, with OR within a facet',()=>{
  const rows=[{id:'a',name:'Alpha',category:'construction',city:'თბილისი',phone:'599123456'},
    {id:'b',name:'Beta',category:'construction',city:'ბათუმი',email:'b@example.ge'},
    {id:'c',name:'Gamma',category:'technology',city:'თბილისი',email:'c@example.ge'},
    {id:'d',name:'Delta',category:'technology',city:'ბათუმი',phone:'599111222'}];
  let result=evaluateFilters(rows,{city:['თბილისი'],category:['construction']});
  assert.deepEqual(result.records.map(r=>r.id),['a']);assert.equal(result.categoryTotal,2);
  assert.equal(result.counts.category.construction,1);assert.equal(result.counts.category.technology,1);
  assert.equal(result.counts.city['ბათუმი'],1);assert.equal(result.counts.contact.email||0,0);
  result=evaluateFilters(rows,{category:['construction','technology'],city:['თბილისი'],contact:['email']});
  assert.deepEqual(result.records.map(r=>r.id),['c']);assert.equal(result.counts.category.construction||0,0);
  result=evaluateFilters(rows,{query:'Alpha',city:['ბათუმი']});
  assert.equal(result.records.length,0);assert.equal(result.counts.city['თბილისი'],1);assert.equal(result.categoryTotal,0);
});

test('registry evidence is distinguishable from a directory entry that merely contains a tax code',()=>{
  const raw={id:'a',name:'Alpha',tax_id:'123456789',activity_code:'71.11.0',activity_name:'არქიტექტურა',
    activity_source_url:'https://br.geostat.ge/?identificationNumber=123456789',registry_verified_at:'2026-11-04'};
  const imported=normalizeImport([raw])[0];assert.equal(imported.activity_code,'71.11.0');assert.ok(registryMatched(imported));
  const directory={id:'b',name:'Beta',tax_id:'987654321'};assert.equal(registryMatched(directory),false);
  assert.deepEqual(evaluateFilters([imported,directory],{registry:['geostat']}).records.map(r=>r.id),['a']);
});


test('manual category corrections override the registry category without claiming verification',()=>{
 const row={id:'a',name:'Alpha',category:'interior',industries:['interior'],classification_override:true,
 activity_code:'74.10.0',activity_source_url:'https://br.geostat.ge/?identificationNumber=123456789'};
 assert.equal(classify(row).category,'interior');assert.equal(classify(row).status,'candidate');
 assert.equal(normalizeImport([row])[0].classification_override,true);
 assert.equal(classify({name:'Alpha',activity_code:'46.1'}).category,'trade');
 assert.equal(classify({name:'Alpha',activity_code:'Z'}).category,'unknown');
});

test('phone and numeric tender filters tolerate display formatting and imported numbers',()=>{
 const rows=[{id:'a',name:'Alpha',phone:'+995 (599) 12-34-56',active_tender:0}];
 assert.equal(evaluateFilters(rows,{query:'599123456',tender:['no']}).records.length,1);
 assert.equal(evaluateFilters(rows,{query:'599 12-34-56',tender:['no']}).records.length,1);
});

test('Tbilisi districts share the city facet without changing the source address',()=>{
 const rows=[{id:'a',name:'Alpha',city:'საბურთალოს რაიონი',address:'Original address'},
 {id:'b',name:'Beta',city:'ძველი თბილისის რაიონი'}, {id:'c',name:'Gamma',city:'ბათუმი'}];
 const result=evaluateFilters(rows,{city:['თბილისი']});
 assert.deepEqual(result.records.map(r=>r.id),['a','b']);assert.equal(result.counts.city['თბილისი'],2);
 assert.equal(rows[0].city,'საბურთალოს რაიონი');assert.equal(rows[0].address,'Original address');
});
