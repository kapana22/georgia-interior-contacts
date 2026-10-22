import {safeCell,safeURL} from '../leads/core.js';
export const categories = {development:'უძრავი ქონების დეველოპმენტი',construction:'მშენებლობა',architecture:'არქიტექტურა',interior:'ინტერიერის დიზაინი',technology:'ტექნოლოგიები',beauty:'სილამაზე',auto:'ავტობიზნესი',food:'კვების ობიექტები',logistics:"საერთაშორისო საავტომობილო გადაზიდვები",forwarding:"ტვირთების ექსპედირება",warehousing:"საბაჟო საწყობები და სატვირთო ტერმინალები",medical_supply:"სამედიცინო აპარატურა და მასალები",building_supply:"სამშენებლო მასალების მომწოდებლები",packaging:"შესაფუთი მასალების საბითუმო ვაჭრობა",metalwork:"ლითონკონსტრუქციების წარმოება და მონტაჟი",healthcare:"კლინიკები და სამედიცინო ცენტრები",laboratories:"სამედიცინო ლაბორატორიები",other:'სხვა',unknown:'მიმართულება დაუზუსტებელია'};
export function categoryIds(record){return [...new Set([classify(record).category,...(record.industries||[]).filter(c=>categories[c])])];}
export const fields = ['source_id','name','legal_status','legal_status_full','tax_id','country','city','address','phone','fax','email','website','role','registered_at','contact_people','active_tender','facebook','instagram','linkedin','youtube','tiktok','social_links','phone_unverified','quality_notes'];
export const labels = ['ID','სრული სახელი','იურ. სტატუსი','სრული იურ. სტატუსი','ID კოდი','ქვეყანა','ქალაქი','მისამართი','ტელეფონი','ფაქსი','ელ-ფოსტა','ვებგვერდი','როლი','რეგისტრაცია','საკონტ. პირები','აქტიური ტენდერი','Facebook','Instagram','LinkedIn','YouTube','TikTok','სხვა სოციალური ბმულები','გადასამოწმებელი ნომერი','მონაცემების შენიშვნა'];
export function publicLinks(record){
  const kinds=['website','facebook','instagram','linkedin','youtube','tiktok','social_links'];
  const inputs=kinds.flatMap(kind=>String(record[kind]||'').split(/[;\n]/).map(value=>({kind,value:value.trim()})));
  for(const f of [...(record.facts||[]),...(record.evidence||[])])if(kinds.includes(f.kind))inputs.push(f);
  const links=new Map();
  for(const item of inputs){const clean=String(item.value||'').trim().replace(/^([a-z][a-z0-9+.-]*:\/\/)\s+/i,'$1');if(!clean)continue;const url=safeURL(/^[a-z][a-z0-9+.-]*:/i.test(clean)?clean:'https://'+clean);if(!url)continue;const parsed=new URL(url),host=parsed.hostname.replace(/^www\./,''),key=host+parsed.pathname.replace(/\/+$/,'').toLowerCase()+parsed.search;const kind=[['facebook','facebook.com'],['instagram','instagram.com'],['linkedin','linkedin.com'],['youtube','youtube.com'],['tiktok','tiktok.com']].find(([,domain])=>host===domain||host.endsWith('.'+domain))?.[0]||item.kind;const prev=links.get(key);if(!prev||(url.startsWith('https:')&&!prev.url.startsWith('https:')))links.set(key,{kind,url});}
  return [...links.values()];
}
export function formatLinkLabel(url){
  try{const u=new URL(url),host=u.hostname.replace(/^www\./,''),path=decodeURIComponent(u.pathname).replace(/\/+$/,'');return path&&path!=='/'?`${host}${path}`:host;}
  catch{return String(url||'');}
}
export function formatPhoneDisplay(raw){
  const text=String(raw||'').trim();
  const compact=text.replace(/[\s()-]/g,'');
  const m12=compact.match(/^\+?995(\d{3})(\d{2})(\d{2})(\d{2})$/);
  if(m12)return `+995 ${m12[1]} ${m12[2]} ${m12[3]} ${m12[4]}`;
  const m9=compact.match(/^(5\d{2})(\d{2})(\d{2})(\d{2})$/);
  if(m9)return `${m9[1]} ${m9[2]} ${m9[3]} ${m9[4]}`;
  return text;
}
export function uniquePhones(value){
  const seen=new Set(),out=[];
  for(const raw of String(value||'').split(/[;\n]+/).map(s=>s.trim()).filter(Boolean)){
    const digits=raw.replace(/\D/g,'').replace(/^995(?=\d{9}$)/,'');
    const key=digits||raw;
    if(seen.has(key))continue;
    seen.add(key);
    const dial=raw.replace(/[^\d+]/g,'');
    out.push({text:formatPhoneDisplay(raw),raw,href:dial.length>=6?`tel:${dial.startsWith('+')||dial.startsWith('995')?(dial.startsWith('+')?dial:'+'+dial):dial}`:''});
  }
  return out;
}
export function uniqueEmails(value){
  const seen=new Set(),out=[];
  for(const raw of String(value||'').split(/[;\n,\s]+/).map(s=>s.trim()).filter(Boolean)){
    const key=raw.toLowerCase();
    if(seen.has(key))continue;
    seen.add(key);
    out.push({text:raw,href:/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)?`mailto:${encodeURIComponent(raw).replace(/%40/g,'@')}`:''});
  }
  return out;
}
export function cleanFax(value){
  const text=String(value||'').trim();
  return !text||text==='+995'?'':text;
}
export function parseContactPeople(value){
  return String(value||'').split(/\r?\n/).map(line=>line.trim()).filter(Boolean).map(line=>{
    const parts=line.split(/\s*·\s*/).filter(Boolean);
    const [head='',...rest]=parts;
    const [name='',role='']=head.split(/\s*—\s*/);
    const phones=[],emails=[],other=[];
    for(const item of rest){
      if(item.includes('@'))emails.push(...uniqueEmails(item));
      else if(/\d{6,}/.test(item.replace(/\D/g,'')))phones.push(...uniquePhones(item));
      else other.push(item);
    }
    return {name:name.trim()||head.trim(),role:role.trim(),phones,emails,other};
  });
}
const norm = value => String(value ?? '').normalize('NFKC').toLocaleLowerCase('ka').trim();
export function cityKey(value) {
  const text=norm(value).split(';')[0].replace(/^[\s!.,\d]+/u,'').replace(/^საქართველო[\s,./-]*/u,'').replace(/^(?:ქალაქი|ქალაქ|ქლაქი|ქ(?=[\s,./-]|თბილისი))[\s,./-]*/u,'');
  if(/^t'?bilisi/u.test(text))return 'თბილისი';
  if(/^batumi/u.test(text))return 'ბათუმი';
  if(/^kutaisi/u.test(text))return 'ქუთაისი';
  if (/^(?:თბილის(?:ი|ის)|თთბილისი)/u.test(text)) return 'თბილისი';
  for(const city of ['ბათუმი','ქუთაისი','რუსთავი','გორი','ზუგდიდი','ფოთი','მცხეთა','ჭიათურა','თელეთი']) if(text.startsWith(city)) return city;
  return String(value??'').split(';')[0].trim();
}
export function classify(record) {
  if (record.category && categories[record.category]) return {category:record.category,status:record.category === 'unknown' ? 'unknown' : record.classification_status === 'confirmed' ? 'confirmed' : 'candidate',reason:record.category_evidence || 'დამატებული მიმართულება'};
  const name = norm(record.name);
  const rules = [
    ['technology', /software|სოფტვეარ|სოფტვერ|პროგრამულ|ვებ დეველოპ|web develop/],
    ['development', /დეველოპ|დეველაფ|\bdevelop(?:ment|er|ers|ments)?\b/],
    ['construction', /მშენებლ|სამშენებლ|კონსტრაქ|ქონსთრაქ|\bconstruction\b/],
    ['architecture', /არქიტექტ|\barchitect/],
    ['interior', /ინტერიერ|\binterior/],
    ['auto', /საავტომობილ|ავტომობილ|ავტოსერვის|ავტოცენტრ|ავტოდილერ|ავტოიმპორტ|ავტომოტორს|ავტონაწილ|ავტოაქსესუარ|ავტოშხაპ|ავტორაიდერ|ავტოგზა|(?:^|\s)ავტო(?:\s|$)|\bauto(?:\s|$|-)|\bcar dealer\b/],
    ['beauty', /სილამაზ|კოსმეტოლოგ|ესთეტიკ|\bbeauty\b|\bhair salon\b|\bnail salon\b|\bbarbershop\b/],
    ['food', /რესტორ|კაფე|საცხობ|პიცერი|კონდიტერ|კულინარ|(?:^|[\s-])ბარი(?:$|[\s-])|\brestaurant\b|\bcafe\b|\bbakery\b|\bpizzeria\b|\bcatering\b/],
  ];
  for (const [category,pattern] of rules) if (pattern.test(name)) return {category,status:'candidate',reason:'სახელის მიხედვით; საქმიანობა გადასამოწმებელია'};
  return {category:'unknown',status:'unknown',reason:'წყაროში საქმიანობა მითითებული არ არის'};
}
export const registrationYear=r=>String(r.registered_at||'').match(/(?:^|[^0-9])((?:19|20)[0-9]{2})(?:[^0-9]|$)/)?.[1]||'unknown';
export function filterRecords(records, filters={}) {
  const tokens=norm(filters.query).split(/\s+/).filter(Boolean);
  const choices=key=>Array.isArray(filters[key])?filters[key].filter(Boolean):filters[key]?[filters[key]]:[];
  const groups=Object.fromEntries(['category','status','city','source','legal','contact','audience','country','role','year','tender','person','site','social'].map(k=>[k,choices(k)]));
  const matches=(key,predicate)=>!groups[key].length||groups[key].some(predicate);
  return records.filter(r=>{
    const c=classify(r);
    return matches('category',v=>categoryIds(r).includes(v)) && matches('status',v=>c.status===v)
      && matches('city',v=>v==='unknown'?!r.city?.trim():cityKey(r.city)===v) && matches('source',v=>r.source===v)
      && matches('legal',v=>v==='unknown' ? !r.legal_status?.trim() : r.legal_status?.trim()===v)
      && matches('audience',v=>v==='interior' ? r.audiences?.includes('interior') || c.category==='interior' : !r.website?.trim() && !!(r.phone?.trim() || r.email?.trim()))
      && matches('contact',v=>v==='both' ? r.phone?.trim() && r.email?.trim() : r[v]?.trim())
      && matches('country',v=>v==='unknown'?!r.country?.trim():r.country?.trim()===v)
      && matches('role',v=>v==='unknown'?!r.role?.trim():r.role?.trim()===v)
      && matches('year',v=>registrationYear(r)===v)
      && matches('person',v=>v==='yes'?!!r.contact_people?.trim():!r.contact_people?.trim())
      && matches('site',v=>v==='yes'?!!r.website?.trim():!r.website?.trim())
      && matches('social',v=>v==='none'?!['facebook','instagram','linkedin','youtube','tiktok','social_links'].some(k=>r[k]?.trim()):v==='any'?['facebook','instagram','linkedin','youtube','tiktok','social_links'].some(k=>r[k]?.trim()):!!r[v]?.trim())
      && matches('tender',v=>v==='unknown'?!r.active_tender?.trim():v==='yes'?Number(r.active_tender)>0:r.active_tender?.trim()==='0')
      && tokens.every(t=>norm(fields.map(f=>r[f]).join(' ')).includes(t));
  });
}
export function exportRows(records, filtered, selected, scope) {
  if(scope==='all')return records;
  if(scope==='selected')return records.filter(r=>selected.has(r.id));
  return filtered;
}
export function sortRecords(records, order) {
  if(!order || order==='original')return records;
  return [...records].sort((a,b)=>String(a[order==='city'?'city':'name']||'').localeCompare(String(b[order==='city'?'city':'name']||''),'ka')*(order==='name-desc'?-1:1));
}
export function normalizeImport(data, source='JSON იმპორტი') {
  const rows=Array.isArray(data)?data:data.records;
  if (!Array.isArray(rows) || !rows.length || rows.length>100000) throw new Error('ფაილში საჭიროა კომპანიების records სია (1–100,000).');
  return rows.map((r,i)=>{
    if (!r || typeof r.name!=='string' || !r.name.trim()) throw new Error(`ჩანაწერი ${i+1}: კომპანიის სახელი აკლია.`);
    const out=Object.fromEntries(fields.map(f=>[f,String(r[f]??'')]));
    // The existing scrapers expose contacts as source-backed facts.
    for(const [field,kind] of [['phone_unverified','phone_unverified'],['phone','phone'],['email','email'],['website','website'],['city','city'],['address','address'],...['facebook','instagram','linkedin','youtube','tiktok','social_links'].map(k=>[k,k])]) {
      if (!out[field] && Array.isArray(r.facts)) out[field]=[...new Set(r.facts.filter(f=>f.kind===kind&&typeof f.value==='string').map(f=>f.value))].join('; ');
    }
    out.source=String(r.source||source);
    if(!r.id && !out.source_id && !out.tax_id.trim()) throw new Error(`ჩანაწერი ${i+1}: საჭიროა სტაბილური id, source_id ან tax_id.`);
    out.id=String(r.id||(out.source_id?`source:${out.source}:${out.source_id}`:`tax:${out.tax_id.trim()}`));
    if(Number.isInteger(r.source_row)) out.source_row=r.source_row;
    if(Array.isArray(r.history)) out.history=r.history;
    if(Array.isArray(r.audiences)) out.audiences=r.audiences.filter(a=>typeof a==='string');
    if(Array.isArray(r.industries)) out.industries=r.industries.filter(c=>categories[c]);
    for(const key of ['category','classification_status','category_evidence']) if(typeof r[key]==='string') out[key]=r[key];
    if(out.category && !categories[out.category]) throw new Error(`უცნობი მიმართულება: ${out.category}`);
    if(out.classification_status==='confirmed' && !out.category_evidence?.trim()) throw new Error('დადასტურებულ მიმართულებას სჭირდება წყარო ან შენიშვნა.');
    out.evidence=Array.isArray(r.facts)?r.facts:Array.isArray(r.evidence)?r.evidence:[];
    out.source_records=Array.isArray(r.source_records)?r.source_records:[r];
    for(const link of publicLinks(out))if(link.kind!=='website')out[link.kind]=[...new Set([...(out[link.kind]||'').split(/;\s*/).filter(Boolean),link.url])].join('; ');
    return out;
  });
}
export function mergeRecords(base, incoming) {
  const records=base.map(r=>({...r}));
  const byId=new Map(records.map((r,i)=>[r.id,i]));
  const byTax=new Map();
  for(let i=0;i<records.length;i++) if(records[i].tax_id?.trim()) {
    const key=records[i].tax_id.trim(); byTax.set(key,byTax.has(key)?null:i);
  }
  for(const row of incoming) {
    const tax=row.tax_id?.trim();
    const index=byId.has(row.id)?byId.get(row.id):tax?byTax.get(tax):undefined;
    if(index===null) throw new Error(`ID კოდი ${tax} რამდენიმე კომპანიას ემთხვევა; საჭიროა ჩანაწერის ID.`);
    if(index!==undefined) {
      const old=records[index];
      if(old.tax_id && tax && old.tax_id.trim()!==tax) throw new Error('ჩანაწერის ID და საიდენტიფიკაციო კოდი ერთმანეთს არ შეესაბამება.');
      const evidence=[...(old.evidence||[]),...(row.evidence||[])];
      records[index]={...old,...Object.fromEntries(Object.entries(row).filter(([,v])=>v!==''&&v!==undefined)),id:old.id,
        source_records:[...new Map([...(old.source_records||[]),...(row.source_records||[])].map(r=>[JSON.stringify(r),r])).values()],
        evidence:[...new Map(evidence.map(e=>[JSON.stringify(e),e])).values()],
        history:[...new Map([...(old.history||[]),...(row.history||[]),Object.fromEntries(Object.entries(row).filter(([key])=>key!=='history'))].map(h=>[JSON.stringify(h),h])).values()],source:old.source};
      byId.set(row.id,index);
      if(tax) byTax.set(tax,index);
    } else {byId.set(row.id,records.length);if(tax)byTax.set(tax,records.length);records.push({...row});}
  }
  return records;
}
export function csv(records, {compact=false, delimiter=','}={}) {
  const quote=v=>'"'+safeCell(v).replaceAll('"','""')+'"';
  const chosen=compact?['name','tax_id','city','phone','email','website']:fields;
  return '\uFEFF'+[ [...chosen.map(f=>labels[fields.indexOf(f)]),'მიმართულება'], ...records.map(r=>[...chosen.map(f=>r[f]??''),categoryIds(r).map(c=>categories[c]).join('; ')] ) ].map(row=>row.map(quote).join(delimiter)).join('\r\n');
}
