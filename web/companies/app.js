import {categories,fields,labels,classify,cityKey,filterRecords,normalizeImport,mergeRecords,csv} from './core.js';
import {safeURL} from '../leads/core.js';
const $=id=>document.getElementById(id), pageSize=30;
let records=[],filtered=[],page=1,editing=null,db;
function el(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function options(id, entries,first){const select=$(id),previous=select.value;select.replaceChildren();if(first){const o=el('option',first);o.value='';select.append(o);}for(const [value,label] of entries){const o=el('option',label);o.value=value;select.append(o);}if([...select.options].some(o=>o.value===previous))select.value=previous;}
function facets(){options('city',[...new Set(records.map(r=>cityKey(r.city)).filter(Boolean))].sort().map(v=>[v,v]),'ყველა ქალაქი');audiences();}

function audiences(){
 const counts=Object.fromEntries(Object.keys(categories).map(k=>[k,0]));for(const r of records)counts[classify(r).category]++;
 options('category',Object.entries(categories).map(([k,v])=>[k,`${v} (${counts[k].toLocaleString()})`]),'ყველა მიმართულება');
 $('audiences').replaceChildren();for(const [key,label] of [['','ყველა კომპანია'],...Object.entries(categories)]){const button=el('button');button.type='button';button.dataset.category=key;button.append(el('span',label),el('strong',(key?counts[key]:records.length).toLocaleString()));button.onclick=()=>{$('category').value=key;page=1;render();};$('audiences').append(button);}
}

function render(){
  filtered=filterRecords(records,Object.fromEntries(['query','category','city','contact'].map(id=>[id,$(id).value])));
  for(const button of $('audiences').children)button.setAttribute('aria-pressed',String(button.dataset.category===$('category').value));
  const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages);
  $('count').textContent=`${filtered.length.toLocaleString()} შედეგი / ${records.length.toLocaleString()} კომპანია`;
  $('csv').disabled=!filtered.length;$('backup').disabled=!records.length;$('results').replaceChildren();
  for(const r of filtered.slice((page-1)*pageSize,page*pageSize)){
    const c=classify(r),card=el('article');card.className='card';card.append(el('h2',r.name));
    const badge=el('p',`${categories[c.category]}`);badge.className='badge';card.append(badge);
    const dl=el('dl');dl.className='facts';
    for(const key of ['tax_id','city','phone','email','website']) if(r[key]){dl.append(el('dt',labels[fields.indexOf(key)]));const dd=el('dd',r[key]);if(key==='website'){const url=safeURL(/^https?:\/\//i.test(r[key])?r[key]:'https://'+r[key]);if(url){const a=el('a',r[key]);a.href=url;a.target='_blank';a.rel='noopener noreferrer';dd.replaceChildren(a);}}dl.append(dd);}card.append(dl);
    const details=el('details');details.append(el('summary','ყველა მონაცემი'));const all=el('dl');all.className='facts';for(let i=0;i<fields.length;i++){all.append(el('dt',labels[i]),el('dd',r[fields[i]]||'—'));}details.append(all);if(r.category_evidence)details.append(el('p',r.category_evidence));card.append(details);
    const edit=el('button','მონაცემების / მიმართულების შეცვლა');edit.onclick=()=>openEditor(r);card.append(edit);$('results').append(card);
  }
  if(!filtered.length)$('results').append(el('p','ამ ფილტრებით კომპანია ვერ მოიძებნა.'));
  $('previous').disabled=page===1;$('next').disabled=page===pages;$('page-label').textContent=`${page} / ${pages}`;
}
function openDB(){return new Promise((resolve,reject)=>{const req=indexedDB.open('company-audiences',1);req.onupgradeneeded=()=>req.result.createObjectStore('records',{keyPath:'id'});req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});}
function savedRows(){return new Promise((resolve,reject)=>{const req=db.transaction('records').objectStore('records').getAll();req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});}
function persist(rows){return new Promise((resolve,reject)=>{if(!db){reject(new Error('ბრაუზერში შენახვა მიუწვდომელია.'));return;}const tx=db.transaction('records','readwrite');for(const row of rows)tx.objectStore('records').put(row);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('შენახვა შეწყდა.'));});}
function openEditor(record){editing=record;const c=record?classify(record):{category:'unknown',status:'candidate',reason:''};$('edit-title').textContent=record?'კომპანიის შეცვლა':'კომპანიის დამატება';$('edit-fields').replaceChildren();for(const key of fields){const label=el('label',labels[fields.indexOf(key)]),input=el('input');input.id='field-'+key;input.value=record?.[key]||'';input.required=key==='name';label.append(input);$('edit-fields').append(label);}$('edit-category').value=c.category;$('edit-evidence').value=record?.category_evidence||'';$('edit-error').textContent='';$('editor').showModal();}
$('edit-form').onsubmit=async event=>{event.preventDefault();try{const row={...(editing||{}),...Object.fromEntries(fields.map(f=>[f,$('field-'+f).value.trim()])),id:editing?.id||'manual:'+crypto.randomUUID(),source:editing?.source||'ხელით დამატებული',category:$('edit-category').value,classification_status:'candidate',category_evidence:$('edit-evidence').value.trim()};normalizeImport([row]);if(row.tax_id&&records.some(r=>r.id!==row.id&&r.tax_id===row.tax_id))throw new Error('ამ ID კოდით კომპანია უკვე არსებობს; მოძებნე და შეცვალე არსებული ჩანაწერი.');await persist([row]);records=records.filter(r=>r.id!==row.id).concat(row);$('editor').close();facets();render();$('message').textContent='ცვლილება შენახულია ამ ბრაუზერში.';}catch(e){$('edit-error').textContent=e.message;}};
$('cancel').onclick=()=>$('editor').close();$('add').onclick=()=>openEditor(null);
function download(content,type,name){const url=URL.createObjectURL(new Blob([content],{type})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('csv').onclick=()=>download(csv(filtered),'text/csv;charset=utf-8','companies-filtered.csv');
$('backup').onclick=()=>download(JSON.stringify({version:1,records},null,2),'application/json','companies-backup.json');
$('import').onchange=async()=>{try{const file=$('import').files[0];if(!file)return;if(file.size>100*1024*1024)throw new Error('ფაილის მაქსიმალური ზომაა 100 MB.');const incoming=normalizeImport(JSON.parse(await file.text()),file.name),next=mergeRecords(records,incoming);const old=new Map(records.map(r=>[r.id,JSON.stringify(r)]));await persist(next.filter(r=>old.get(r.id)!==JSON.stringify(r)));const added=next.length-records.length;records=next;facets();render();$('error').hidden=true;$('message').textContent=`დამუშავებულია ${incoming.length}; ახალი კომპანია: ${added}. შენახულია ამ ბრაუზერში.`;}catch(e){$('error').hidden=false;$('error').textContent=e.message;}finally{$('import').value='';}};
options('category',Object.entries(categories),'ყველა მიმართულება');options('edit-category',Object.entries(categories));
$('filters').onsubmit=e=>e.preventDefault();for(const id of ['query','category','city','contact'])$(id).addEventListener(id==='query'?'input':'change',()=>{page=1;render();});$('filters').onreset=()=>setTimeout(()=>{page=1;render();},0);
$('developers').onclick=()=>{$('category').value='development';page=1;render();};$('previous').onclick=()=>{page--;render();};$('next').onclick=()=>{page++;render();};
$('add').disabled=true;
try{const response=await fetch('/companies/data.json');if(!response.ok)throw new Error('კომპანიების ბაზა ვერ ჩაიტვირთა.');const data=await response.json();if(data.version!==1||!Array.isArray(data.records))throw new Error('ბაზის ფორმატი არასწორია.');records=data.records;try{db=await openDB();const saved=await savedRows(),map=new Map(records.map(r=>[r.id,r]));for(const r of saved)map.set(r.id,r);records=[...map.values()];$('import').disabled=false;$('add').disabled=false;}catch{$('message').textContent='ბრაუზერში შენახვა მიუწვდომელია; ბაზის დათვალიერება და ჩამოტვირთვა შესაძლებელია.';}facets();render();}catch(e){$('error').hidden=false;$('error').textContent=e.message;$('count').textContent='ბაზა ვერ ჩაიტვირთა';}finally{$('results').setAttribute('aria-busy','false');}
