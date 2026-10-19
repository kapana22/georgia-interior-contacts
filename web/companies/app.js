import {fetchCompanySnapshot,overlaySavedRows} from './sync.js';
import {categories,categoryIds,publicLinks,fields,labels,classify,cityKey,registrationYear,filterRecords,normalizeImport,mergeRecords,csv,sortRecords,exportRows} from './core.js';
import {safeURL} from '../leads/core.js';
const $=id=>document.getElementById(id);
let pageSize=30;const selected=new Set();let detailRecord=null;
let records=[],filtered=[],page=1,editing=null,db;
function el(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function options(id, entries,first){const select=$(id),previous=select.value;select.replaceChildren();if(first){const o=el('option',first);o.value='';select.append(o);}for(const [value,label] of entries){const o=el('option',label);o.value=value;select.append(o);}if([...select.options].some(o=>o.value===previous))select.value=previous;}
const filterNames={category:'მიმართულება',city:'ქალაქი',contact:'კონტაქტი',audience:'აუდიტორია',legal:'იურიდიული ფორმა',country:'ქვეყანა',role:'როლი',year:'რეგისტრაციის წელი',tender:'აქტიური ტენდერი',person:'საკონტაქტო პირი',site:'ვებსაიტი',social:'სოციალური პროფილები'};
const chosen=Object.fromEntries(Object.keys(filterNames).map(k=>[k,new Set()]));
const filterOptions={category:Object.entries(categories),city:[],contact:[['phone','ტელეფონით'],['email','ელფოსტით'],['both','ტელეფონი და ელფოსტა'],['website','ვებსაიტით']],audience:[['interior','ინტერიერის კონტაქტები'],['website','ვებსაიტის კლიენტები']],legal:[],country:[],role:[],year:[],tender:[['yes','აქვს'],['no','არ აქვს'],['unknown','არ არის მითითებული']],person:[['yes','მითითებულია'],['no','არ არის მითითებული']],site:[['yes','მითითებულია'],['no','არ არის მითითებული']],social:[['any','აქვს სოციალური ბმული'],['none','არ არის მითითებული'],['facebook','Facebook'],['instagram','Instagram'],['linkedin','LinkedIn'],['youtube','YouTube'],['tiktok','TikTok']]};
let optionCounts={};
function toggleChoice(key,value,checked){checked?chosen[key].add(value):chosen[key].delete(value);page=1;syncFilters();render();}
function buildFilter(key){
 const panel=$(key),list=panel.querySelector('.filter-options');list.replaceChildren();
 for(const [value,name] of filterOptions[key]){const label=el('label');label.className='filter-option';const input=el('input');input.type='checkbox';input.value=value;input.checked=chosen[key].has(value);input.onchange=()=>toggleChoice(key,value,input.checked);const count=el('small',(optionCounts[key]?.[value]||0).toLocaleString());count.className='option-count';label.append(input,el('span',name),count);list.append(label);}
 const search=panel.querySelector('.option-search');search.value='';search.hidden=filterOptions[key].length<6;search.oninput=()=>{let found=0;for(const label of list.children){label.hidden=!label.textContent.toLocaleLowerCase('ka').includes(search.value.toLocaleLowerCase('ka').trim());if(!label.hidden)found++;}panel.querySelector('.no-options').hidden=!!found;};
 panel.querySelector('.group-clear').onclick=()=>{chosen[key].clear();page=1;syncFilters();render();};
}
function syncFilters(){
 $('active-filters').replaceChildren();
 for(const [key,title] of Object.entries(filterNames)){
  const names=filterOptions[key].filter(([value])=>chosen[key].has(value)).map(([,name])=>name);
  $(key).querySelector('.filter-summary').textContent=names.length===0?'ყველა':names.length===1?names[0]:`არჩეულია ${names.length}`;
  $(key).classList.toggle('has-selection',!!names.length);
  for(const input of $(key).querySelectorAll('input[type=checkbox]'))input.checked=chosen[key].has(input.value);
  for(const [value,name] of filterOptions[key])if(chosen[key].has(value)){const chip=el('button',`${title}: ${name} ×`);chip.type='button';chip.setAttribute('aria-label',`${name} — ფილტრის მოხსნა`);chip.onclick=()=>toggleChoice(key,value,false);$('active-filters').append(chip);}
 }
 for(const input of $('audiences').querySelectorAll('input'))input.checked=chosen.category.has(input.value);
 const all=$('audiences').querySelector('button');if(all)all.setAttribute('aria-pressed',String(!chosen.category.size));
}
function facets(){
 filterOptions.city=[...new Set(records.map(r=>cityKey(r.city)).filter(Boolean))].sort().map(v=>[v,v]);
 filterOptions.legal=[...new Set(records.map(r=>r.legal_status?.trim()).filter(Boolean))].sort().map(v=>[v,v]);filterOptions.legal.push(['unknown','არ არის მითითებული']);
 filterOptions.city.push(['unknown','არ არის მითითებული']);
 for(const key of ['country','role'])filterOptions[key]=[...[...new Set(records.map(r=>r[key]?.trim()).filter(Boolean))].sort().map(v=>[v,v]),['unknown','არ არის მითითებული']];
 filterOptions.year=[...new Set(records.map(registrationYear).filter(v=>v!=='unknown'))].sort().reverse().map(v=>[v,v]);filterOptions.year.push(['unknown','არ არის მითითებული']);
 optionCounts=Object.fromEntries(Object.keys(filterNames).map(k=>[k,{}]));
 for(const r of records){
  const social=['facebook','instagram','linkedin','youtube','tiktok','social_links'].filter(k=>r[k]?.trim());
  const values={category:categoryIds(r),city:[cityKey(r.city)||'unknown'],legal:[r.legal_status?.trim()||'unknown'],country:[r.country?.trim()||'unknown'],role:[r.role?.trim()||'unknown'],year:[registrationYear(r)],tender:[!r.active_tender?.trim()?'unknown':Number(r.active_tender)>0?'yes':r.active_tender.trim()==='0'?'no':''],person:[r.contact_people?.trim()?'yes':'no'],site:[r.website?.trim()?'yes':'no'],social:[...social,social.length?'any':'none'],contact:[...['phone','email','website'].filter(k=>r[k]?.trim()),...(r.phone?.trim()&&r.email?.trim()?['both']:[])],audience:[...(r.audiences?.includes('interior')||classify(r).category==='interior'?['interior']:[]),...(!r.website?.trim()&&(r.phone?.trim()||r.email?.trim())?['website']:[])]};
  for(const [key,list] of Object.entries(values))for(const value of list)optionCounts[key][value]=(optionCounts[key][value]||0)+1;
 }
 for(const key of Object.keys(filterNames))buildFilter(key);audiences();syncFilters();
}
function audiences(){
 const counts=Object.fromEntries(Object.keys(categories).map(k=>[k,0]));for(const r of records)for(const c of categoryIds(r))counts[c]++;
 $('audiences').replaceChildren();const all=el('button');all.type='button';all.append(el('span','ყველა კომპანია'),el('strong',records.length.toLocaleString()));all.onclick=()=>{chosen.category.clear();page=1;syncFilters();render();};$('audiences').append(all);
 for(const [key,name] of Object.entries(categories)){const label=el('label');label.className='audience-choice';const input=el('input');input.type='checkbox';input.value=key;input.checked=chosen.category.has(key);input.setAttribute('aria-label',name+' — მიმართულება');input.onchange=()=>toggleChoice('category',key,input.checked);label.append(input,el('span',name),el('strong',counts[key].toLocaleString()));$('audiences').append(label);}
}

function render(){
  filtered=filterRecords(records,{query:$('query').value,...Object.fromEntries(Object.entries(chosen).map(([k,v])=>[k,[...v]]))});
  filtered=sortRecords(filtered,$('sort').value);
  const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages);
  $('count').textContent=`${filtered.length.toLocaleString()} შედეგი / ${records.length.toLocaleString()} კომპანია`;
  $('export-open').disabled=!records.length;$('results').replaceChildren();
  for(const r of filtered.slice((page-1)*pageSize,page*pageSize)){
    const c=classify(r),card=el('article');card.className='card';card.append(selection(r),el('h2',r.name));
    const badge=el('p',`${categoryIds(r).map(c=>categories[c]).join(' · ')}`);badge.className='badge';card.append(badge);
    const dl=el('dl');dl.className='facts';
    for(const key of ['tax_id','city','phone','phone_unverified','quality_notes','email','website','facebook','instagram','linkedin','youtube','tiktok','social_links']) if(r[key]){dl.append(el('dt',labels[fields.indexOf(key)]));const dd=el('dd',r[key]);if(['website','facebook','instagram','linkedin','youtube','tiktok','social_links'].includes(key)){const url=safeURL(/^https?:\/\//i.test(r[key])?r[key]:'https://'+r[key]);if(url){const a=el('a',r[key]);a.href=url;a.target='_blank';a.rel='noopener noreferrer';dd.replaceChildren(a);}}dl.append(dd);}card.append(dl);
    const details=el('details');details.append(el('summary','ყველა მონაცემი'));const all=el('dl');all.className='facts';for(let i=0;i<fields.length;i++){all.append(el('dt',labels[i]),el('dd',r[fields[i]]||'—'));}details.append(all);if(r.category_evidence)details.append(el('p',r.category_evidence));card.append(details);
    const edit=el('button','მონაცემების / მიმართულების შეცვლა');edit.onclick=()=>openEditor(r);card.append(edit);$('results').append(card);
  }
  if($('view').value==='table')renderTable();
  $('results').classList.toggle('table-view',$('view').value==='table');
  updateSelection();
  if(!filtered.length)$('results').append(el('p','ამ ფილტრებით კომპანია ვერ მოიძებნა.'));
  $('previous').disabled=page===1;$('next').disabled=page===pages;$('page-label').textContent=`${page} / ${pages}`;
}
function openDB(){return new Promise((resolve,reject)=>{const req=indexedDB.open('company-audiences',1);req.onupgradeneeded=()=>req.result.createObjectStore('records',{keyPath:'id'});req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});}
function savedRows(){return new Promise((resolve,reject)=>{const req=db.transaction('records').objectStore('records').getAll();req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});}
function persist(rows){return new Promise((resolve,reject)=>{if(!db){reject(new Error('ბრაუზერში შენახვა მიუწვდომელია.'));return;}const tx=db.transaction('records','readwrite');for(const row of rows)tx.objectStore('records').put(row);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('შენახვა შეწყდა.'));});}
function openEditor(record){editing=record;const c=record?classify(record):{category:'unknown',status:'candidate',reason:''};$('edit-title').textContent=record?'კომპანიის შეცვლა':'კომპანიის დამატება';$('edit-fields').replaceChildren();for(const key of fields){const label=el('label',labels[fields.indexOf(key)]),input=el('input');input.id='field-'+key;input.value=record?.[key]||'';input.required=key==='name';label.append(input);$('edit-fields').append(label);}$('edit-category').value=c.category;$('edit-evidence').value=record?.category_evidence||'';$('edit-error').textContent='';$('editor').showModal();}
$('edit-form').onsubmit=async event=>{event.preventDefault();try{const row={...(editing||{}),...Object.fromEntries(fields.map(f=>[f,$('field-'+f).value.trim()])),id:editing?.id||'manual:'+crypto.randomUUID(),source:editing?.source||'ხელით დამატებული',category:$('edit-category').value,industries:[$('edit-category').value],classification_status:'candidate',category_evidence:$('edit-evidence').value.trim()};normalizeImport([row]);if(row.tax_id&&records.some(r=>r.id!==row.id&&r.tax_id===row.tax_id))throw new Error('ამ ID კოდით კომპანია უკვე არსებობს; მოძებნე და შეცვალე არსებული ჩანაწერი.');await persist([row]);records=records.filter(r=>r.id!==row.id).concat(row);$('editor').close();facets();render();$('message').textContent='ცვლილება შენახულია ამ ბრაუზერში.';}catch(e){$('edit-error').textContent=e.message;}};
$('cancel').onclick=()=>$('editor').close();$('add').onclick=()=>openEditor(null);
function download(content,type,name){const url=URL.createObjectURL(new Blob([content],{type})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('import').onchange=async()=>{try{const file=$('import').files[0];if(!file)return;if(file.size>100*1024*1024)throw new Error('ფაილის მაქსიმალური ზომაა 100 MB.');const incoming=normalizeImport(JSON.parse(await file.text()),file.name),next=mergeRecords(records,incoming);const old=new Map(records.map(r=>[r.id,JSON.stringify(r)]));await persist(next.filter(r=>old.get(r.id)!==JSON.stringify(r)));const added=next.length-records.length;records=next;facets();render();$('error').hidden=true;$('message').textContent=`დამუშავებულია ${incoming.length}; ახალი კომპანია: ${added}. შენახულია ამ ბრაუზერში.`;}catch(e){$('error').hidden=false;$('error').textContent=e.message;}finally{$('import').value='';}};
options('edit-category',Object.entries(categories));
$('filters').onsubmit=e=>e.preventDefault();$('query').oninput=()=>{page=1;render();};$('filters').onreset=()=>{for(const set of Object.values(chosen))set.clear();setTimeout(()=>{for(const key of Object.keys(filterNames)){const search=$(key).querySelector('.option-search');search.value='';search.dispatchEvent(new Event('input'));}page=1;syncFilters();render();},0);};
$('developers').onclick=()=>{chosen.category.clear();chosen.category.add('development');page=1;syncFilters();render();};
for(const key of Object.keys(filterNames)){$(key).addEventListener('toggle',()=>{if($(key).open)for(const other of Object.keys(filterNames))if(other!==key)$(other).open=false;});$(key).addEventListener('keydown',e=>{if(e.key==='Escape'){$(key).open=false;$(key).querySelector('summary').focus();}});}
document.addEventListener('click',e=>{for(const key of Object.keys(filterNames))if(!$(key).contains(e.target))$(key).open=false;});
$('previous').onclick=()=>{page--;render();};$('next').onclick=()=>{page++;render();};
function selection(r){const input=el('input');input.type='checkbox';input.className='row-check';input.checked=selected.has(r.id);input.dataset.id=r.id;input.setAttribute('aria-label',r.name+' — მონიშვნა');input.onchange=()=>{input.checked?selected.add(r.id):selected.delete(r.id);updateSelection();};return input;}
function updateSelection(){const visible=filtered.slice((page-1)*pageSize,page*pageSize),count=visible.filter(r=>selected.has(r.id)).length;$('select-page').checked=!!visible.length&&count===visible.length;$('select-page').indeterminate=count>0&&count<visible.length;$('select-page').disabled=!visible.length;$('select-filtered').disabled=!filtered.length;$('clear-selection').hidden=!selected.size;$('selected-count').textContent=selected.size?`მონიშნულია ${selected.size.toLocaleString()}`:'';}
function showDetails(r){detailRecord=r;$('detail-title').textContent=r.name;const dl=el('dl');dl.className='facts';for(let i=0;i<fields.length;i++){const dd=el('dd',r[fields[i]]||'—');if(['website','facebook','instagram','linkedin','youtube','tiktok','social_links'].includes(fields[i])&&r[fields[i]]){dd.replaceChildren();for(const value of r[fields[i]].split(/;\s*/)){const a=el('a',value),url=safeURL(/^[a-z][a-z0-9+.-]*:/i.test(value)?value:'https://'+value);if(url){a.href=url;a.target='_blank';a.rel='noopener noreferrer';}dd.append(a,el('br'));}}dl.append(el('dt',labels[i]),dd);}$('detail-body').replaceChildren(dl);$('detail-dialog').showModal();}
function renderTable(){const table=el('table');table.className='companies-table';const head=el('thead'),tr=el('tr');for(const text of ['მონიშვნა','კომპანია','მიმართულება','ქალაქი','კონტაქტები','']){const th=el('th',text);th.scope='col';tr.append(th);}head.append(tr);table.append(head);const body=el('tbody');for(const r of filtered.slice((page-1)*pageSize,page*pageSize)){const row=el('tr'),check=el('td');check.append(selection(r));const company=el('td'),name=el('button',r.name);name.className='company-name';name.onclick=()=>showDetails(r);company.append(name,el('small',[r.legal_status,r.tax_id].filter(Boolean).join(' · ')||'ID კოდი არ არის მითითებული'));const category=el('td',categoryIds(r).map(c=>categories[c]).join(' · ')),city=el('td',cityKey(r.city)||'—'),contacts=el('td');for(const key of ['phone','email'])if(r[key])contacts.append(el('div',r[key]));if(r.phone_unverified)contacts.append(el('small','გადასამოწმებელი ნომერი: '+r.phone_unverified));if(r.quality_notes)contacts.append(el('small',r.quality_notes));if(!r.phone&&!r.email&&!r.phone_unverified)contacts.append(el('span','—'));const links=el('div');links.className='social-links';for(const link of publicLinks(r)){const a=el('a',labels[fields.indexOf(link.kind)]||'ბმული');a.href=link.url;a.target='_blank';a.rel='noopener noreferrer';links.append(a);}contacts.append(links);const action=el('td'),button=el('button','ნახვა');button.onclick=()=>showDetails(r);action.append(button);row.append(check,company,category,city,contacts,action);body.append(row);}table.append(body);$('results').replaceChildren(table);}
$('select-page').onchange=()=>{for(const r of filtered.slice((page-1)*pageSize,page*pageSize))$('select-page').checked?selected.add(r.id):selected.delete(r.id);render();};
$('select-filtered').onclick=()=>{for(const r of filtered)selected.add(r.id);render();};$('clear-selection').onclick=()=>{selected.clear();render();};
$('detail-close').onclick=()=>$('detail-dialog').close();$('detail-edit').onclick=()=>{$('detail-dialog').close();openEditor(detailRecord);};
function exportPreview(){const rows=exportRows(records,filtered,selected,$('export-scope').value);$('export-count').textContent=`ჩამოიტვირთება ${rows.length.toLocaleString()} კომპანია`;$('export-submit').disabled=!rows.length;}
$('export-open').onclick=()=>{$('export-scope').value='filtered';exportPreview();$('export-dialog').showModal();};$('export-close').onclick=()=>$('export-dialog').close();$('export-scope').onchange=exportPreview;
$('export-form').onsubmit=event=>{event.preventDefault();const scope=$('export-scope').value,rows=exportRows(records,filtered,selected,scope),format=$('export-format').value;if(!rows.length)return;const date=new Date().toISOString().slice(0,10),filename=`companies-${scope}-${rows.length}-${date}`;download(format==='json'?JSON.stringify({version:1,records:rows},null,2):csv(rows,{compact:format==='contacts'}),format==='json'?'application/json':'text/csv;charset=utf-8',filename+(format==='json'?'.json':'.csv'));$('export-dialog').close();$('message').textContent=`ჩამოტვირთულია ${rows.length.toLocaleString()} კომპანიის სია.`;};

$('add').disabled=true;
$('sort').onchange=()=>{page=1;render();};$('view').onchange=render;$('page-size').onchange=()=>{pageSize=Number($('page-size').value);page=1;render();};
let refreshing=false,lastSnapshot='';
const liveBase='https://raw.githubusercontent.com/kapana22/georgia-interior-contacts/companies-data/';
async function loadCompanyData(fallback=false){
 try{return await fetchCompanySnapshot(liveBase+'data.json?refresh='+Math.floor(Date.now()/90000));}
 catch(error){if(!fallback)throw error;return fetchCompanySnapshot('/companies/data.json');}
}
async function refreshCompanies(initial=false){
 if(refreshing||(!initial&&(document.hidden||document.querySelector('dialog[open]'))))return;
 refreshing=true;
 try{
  const data=await loadCompanyData(initial),signature=JSON.stringify(data);
  if(initial){try{db=await openDB();$('import').disabled=false;$('add').disabled=false;}catch{$('message').textContent='ბრაუზერში შენახვა მიუწვდომელია; ბაზის დათვალიერება და ჩამოტვირთვა შესაძლებელია.';}}
  if(signature!==lastSnapshot){
   records=overlaySavedRows(data.records,db?await savedRows():[]);
   lastSnapshot=signature;facets();render();
  }
  $('error').hidden=true;
  const stamp=new Date(data.updated_at);
  $('sync-status').textContent=`ბაზა მოწმდება ყოველ 90 წამში · ბოლო მონაცემები: ${Number.isNaN(stamp.valueOf())?'თარიღი უცნობია':stamp.toLocaleString('ka-GE')}`;
 }catch(error){
  if(initial){$('error').hidden=false;$('error').textContent=error.message;$('count').textContent='ბაზა ვერ ჩაიტვირთა';}
  else $('sync-status').textContent='განახლება დროებით ვერ მოხერხდა; არსებული სია შენარჩუნებულია. ავტომატურად ვცდით ხელახლა.';
 }finally{refreshing=false;$('results').setAttribute('aria-busy','false');}
}
await refreshCompanies(true);
setInterval(()=>refreshCompanies(),90000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshCompanies();});
