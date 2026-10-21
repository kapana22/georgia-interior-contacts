import {fetchCompanySnapshot,overlaySavedRows,createLiveLoader} from './sync.js';
import {categories,categoryIds,publicLinks,formatLinkLabel,formatPhoneDisplay,uniquePhones,uniqueEmails,cleanFax,parseContactPeople,fields,labels,classify,cityKey,registrationYear,filterRecords,normalizeImport,mergeRecords,csv,sortRecords,exportRows} from './core.js';
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

function contactRow(tag,content){
  const row=el('div');row.className='c-row';
  const label=el('span',tag);label.className='c-tag';
  const vals=el('div');vals.className='c-vals';
  if(typeof content==='string'){
    const text=content.trim();
    if(!text||text==='—'){const empty=el('span','—');empty.className='empty-val';vals.append(empty);}
    else vals.textContent=text;
  }else if(Array.isArray(content)){
    if(!content.length){const empty=el('span','—');empty.className='empty-val';vals.append(empty);}
    else vals.append(...content);
  }else if(content)vals.append(content);
  row.append(label,vals);return row;
}
function reqChip(label,value){
  const chip=el('span');chip.className='req-chip';
  const b=el('b',label+': ');
  chip.append(b,document.createTextNode(value||'—'));
  return chip;
}
function statusLine(label,value){
  const row=el('div');row.className='st-row';
  const k=el('span',label+':');k.className='st-k';
  const v=el('span',value||'—');v.className='st-v';
  row.append(k,v);return row;
}
function phoneItems(value){
  return uniquePhones(value).map(p=>{
    if(!p.href)return el('span',p.text);
    const a=el('a',p.text);a.href=p.href;a.className='contact-link';return a;
  });
}
function emailItems(value){
  return uniqueEmails(value).map(e=>{
    if(!e.href)return el('span',e.text);
    const a=el('a',e.text);a.href=e.href;a.className='contact-link';return a;
  });
}
function websiteItems(r){
  return publicLinks(r).filter(l=>l.kind==='website').map(l=>{
    const a=el('a',formatLinkLabel(l.url)+' ↗');a.href=l.url;a.target='_blank';a.rel='noopener noreferrer';a.className='ext-link';return a;
  });
}
function socialItems(r){
  return publicLinks(r).filter(l=>l.kind!=='website').map(l=>{
    const title=labels[fields.indexOf(l.kind)]||'ბმული';
    const a=el('a',`${title}: ${formatLinkLabel(l.url)} ↗`);a.href=l.url;a.target='_blank';a.rel='noopener noreferrer';a.className='ext-link social-pill';return a;
  });
}
function peopleNode(value){
  const people=parseContactPeople(value);
  if(!people.length){const empty=el('span','არ არის მითითებული');empty.className='empty-val';return empty;}
  const wrap=el('div');wrap.className='people-list';
  for(const p of people){
    const item=el('div');item.className='person-card';
    const head=el('div');head.className='person-head';
    head.append(el('strong',p.name));
    if(p.role){const role=el('span',p.role);role.className='person-role';head.append(role);}
    item.append(head);
    const contacts=el('div');contacts.className='person-contacts';
    for(const ph of p.phones){const a=el(ph.href?'a':'span',ph.text);if(ph.href)a.href=ph.href;a.className='contact-link';contacts.append(a);}
    for(const em of p.emails){const a=el(em.href?'a':'span',em.text);if(em.href)a.href=em.href;a.className='contact-link';contacts.append(a);}
    for(const ot of p.other)contacts.append(el('span',ot));
    if(contacts.childElementCount)item.append(contacts);
    wrap.append(item);
  }
  return wrap;
}
function tenderText(value){
  const t=String(value??'').trim();
  if(!t)return '—';
  if(t==='0')return '0 (არ აქვს)';
  return Number(t)>0?`${t} (აქტიური)`:t;
}
function categoryBadges(r){
  const row=el('div');row.className='badge-row';
  for(const cid of categoryIds(r)){
    const b=el('span',categories[cid]);
    b.className=cid==='unknown'?'badge badge-subtle':'badge';
    row.append(b);
  }
  return row;
}
function contactsStack(r){
  const stack=el('div');stack.className='c-stack';
  stack.append(
    contactRow('ტელ',phoneItems(r.phone)),
    contactRow('ფოსტა',emailItems(r.email)),
    contactRow('საიტი',websiteItems(r))
  );
  const fax=cleanFax(r.fax);if(fax)stack.append(contactRow("ფაქსი",formatPhoneDisplay(fax)));
  if(r.phone_unverified)stack.append(contactRow('სხვა ტელ',r.phone_unverified));
  const socials=socialItems(r);if(socials.length)stack.append(contactRow('სოც.',socials));
  return stack;
}
function locationBlock(r){
  const wrap=el('div');wrap.className='loc-block';
  const main=el('div');main.className='loc-main';
  main.append(el('strong',cityKey(r.city)||r.city||'—'));
  if(r.country){const c=el('span',r.country);c.className='loc-country';main.append(c);}
  const addr=el('div',r.address||'მისამართი არ არის მითითებული');
  addr.className=r.address?'loc-addr':'loc-addr empty-val';
  wrap.append(main,addr);
  return wrap;
}
function renderCard(r){
  const card=el('article');card.className='card';
  const header=el('div');header.className='card-header';
  const titleWrap=el('div');titleWrap.className='card-title-wrap';
  const topLine=el('div');topLine.className='card-top-line';
  topLine.append(selection(r),el('h2',r.name),categoryBadges(r));
  titleWrap.append(topLine);
  if(r.legal_status_full||r.legal_status){
    const legal=el('p',r.legal_status_full||r.legal_status);
    legal.className='card-legal-sub';
    titleWrap.append(legal);
  }

  const headerMeta=el('div');headerMeta.className='card-header-meta';
  const pills=el('div');pills.className='req-row';
  pills.append(
    reqChip('ს/კ',r.tax_id),
    reqChip('ID',r.source_id),
    reqChip('როლი',r.role),
    reqChip('რეგისტრაცია',r.registered_at),
    reqChip('ტენდერი',tenderText(r.active_tender))
  );
  const edit=el('button','რედაქტირება');edit.type='button';edit.className='edit-btn';edit.onclick=()=>openEditor(r);
  headerMeta.append(pills,edit);
  header.append(titleWrap,headerMeta);
  card.append(header);

  const grid=el('div');grid.className='card-grid';

  const secContacts=el('section');secContacts.className='card-section';
  secContacts.append(el('h3','კონტაქტები და ბმულები'),contactsStack(r));

  const secLocation=el('section');secLocation.className='card-section';
  secLocation.append(el('h3','მდებარეობა და მისამართი'),locationBlock(r));

  const secPeople=el('section');secPeople.className='card-section';
  secPeople.append(el('h3','საკონტაქტო პირები'),peopleNode(r.contact_people));

  grid.append(secContacts,secLocation,secPeople);
  card.append(grid);

  if(r.quality_notes||r.category_evidence){
    const notes=el('div');notes.className='card-notes';
    if(r.quality_notes)notes.append(reqChip('შენიშვნა',r.quality_notes));
    if(r.category_evidence)notes.append(reqChip('საფუძველი',r.category_evidence));
    card.append(notes);
  }
  return card;
}
function render(){
  filtered=filterRecords(records,{query:$('query').value,...Object.fromEntries(Object.entries(chosen).map(([k,v])=>[k,[...v]]))});
  filtered=sortRecords(filtered,$('sort').value);
  const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages);
  $('count').textContent=`${filtered.length.toLocaleString()} შედეგი / ${records.length.toLocaleString()} კომპანია`;
  $('export-open').disabled=!records.length;$('results').replaceChildren();
  if($('view').value==='table')renderTable();
  else for(const r of filtered.slice((page-1)*pageSize,page*pageSize))$('results').append(renderCard(r));
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
function renderTable(){
  const table=el('table');table.className='companies-table';
  const head=el('thead'),tr=el('tr');
  for(const [i,text] of ['✓','კომპანია და რეკვიზიტები','მდებარეობა და მისამართი','კონტაქტები და ბმულები','საკონტაქტო პირები','სტატუსი და მართვა'].entries()){
    const th=el('th',text);th.scope='col';if(i===0)th.setAttribute('aria-label','მონიშვნა');tr.append(th);
  }
  head.append(tr);table.append(head);
  const body=el('tbody');
  for(const r of filtered.slice((page-1)*pageSize,page*pageSize)){
    const row=el('tr');
    const check=el('td');check.append(selection(r));

    const company=el('td');
    const title=el('strong',r.name);title.className='company-title';
    const reqs=el('div');reqs.className='req-row';
    if(r.legal_status)reqs.append(reqChip('ფორმა',r.legal_status));
    reqs.append(reqChip('ს/კ',r.tax_id),reqChip('ID',r.source_id));
    company.append(title,categoryBadges(r),reqs);
    if(r.legal_status_full&&r.legal_status_full!==r.legal_status){
      const full=el('div',r.legal_status_full);full.className='legal-sub';company.append(full);
    }

    const location=el('td');
    location.append(locationBlock(r));

    const contacts=el('td');
    contacts.append(contactsStack(r));

    const people=el('td');
    people.append(peopleNode(r.contact_people));

    const status=el('td');
    const stList=el('div');stList.className='st-list';
    stList.append(
      statusLine('როლი',r.role),
      statusLine('რეგისტრაცია',r.registered_at),
      statusLine('ტენდერი',tenderText(r.active_tender))
    );
    if(r.quality_notes)stList.append(statusLine('შენიშვნა',r.quality_notes));
    if(r.category_evidence)stList.append(statusLine('საფუძველი',r.category_evidence));
    const edit=el('button','რედაქტირება');edit.type='button';edit.className='edit-btn';edit.onclick=()=>openEditor(r);
    status.append(stList,edit);

    row.append(check,company,location,contacts,people,status);
    body.append(row);
  }
  table.append(body);
  $('results').replaceChildren(table);
}
$('select-page').onchange=()=>{for(const r of filtered.slice((page-1)*pageSize,page*pageSize))$('select-page').checked?selected.add(r.id):selected.delete(r.id);render();};
$('select-filtered').onclick=()=>{for(const r of filtered)selected.add(r.id);render();};$('clear-selection').onclick=()=>{selected.clear();render();};
$('detail-close').onclick=()=>$('detail-dialog').close();$('detail-edit').onclick=()=>{$('detail-dialog').close();openEditor(detailRecord);};
function exportPreview(){const rows=exportRows(records,filtered,selected,$('export-scope').value);$('export-count').textContent=`ჩამოიტვირთება ${rows.length.toLocaleString()} კომპანია`;$('export-submit').disabled=!rows.length;}
$('export-open').onclick=()=>{$('export-scope').value='filtered';exportPreview();$('export-dialog').showModal();};$('export-close').onclick=()=>$('export-dialog').close();$('export-scope').onchange=exportPreview;
$('export-form').onsubmit=event=>{event.preventDefault();const scope=$('export-scope').value,rows=exportRows(records,filtered,selected,scope),format=$('export-format').value;if(!rows.length)return;const date=new Date().toISOString().slice(0,10),filename=`companies-${scope}-${rows.length}-${date}`;download(format==='json'?JSON.stringify({version:1,records:rows},null,2):csv(rows,{compact:format==='contacts'}),format==='json'?'application/json':'text/csv;charset=utf-8',filename+(format==='json'?'.json':'.csv'));$('export-dialog').close();$('message').textContent=`ჩამოტვირთულია ${rows.length.toLocaleString()} კომპანიის სია.`;};

$('add').disabled=true;
$('sort').onchange=()=>{page=1;render();};$('view').onchange=render;$('page-size').onchange=()=>{pageSize=Number($('page-size').value);page=1;render();};
let refreshing=false,lastSnapshot='';
const loadLiveSnapshot=createLiveLoader();
async function loadCompanyData(fallback=false){
 try{return await loadLiveSnapshot();}
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
