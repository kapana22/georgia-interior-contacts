import {categories,categoryIds,classify,cityKey,registrationYear,fields} from './core.js';

const cache=new WeakMap();
const normalize=value=>String(value??'').normalize('NFKC').toLocaleLowerCase('ka');
export function registryMatched(record){
  if(record.registry_verified_at&&/^https:\/\/br\.geostat\.ge\//.test(record.activity_source_url||''))return true;
  return (record.evidence||record.facts||[]).some(f=>f.kind==='tax_id'&&String(f.value)===record.tax_id&&/^https:\/\/br\.geostat\.ge\//.test(f.source_url||''));
}
function indexed(record){
  let value=cache.get(record);if(value)return value;
  const present=key=>!!String(record[key]??'').trim();
  const social=['facebook','instagram','linkedin','youtube','tiktok','social_links'].filter(present);
  const classification=classify(record);
  const values={category:categoryIds(record),status:[classification.status],source:[record.source||''],
    city:[cityKey(record.city)||'unknown'],legal:[record.legal_status?.trim()||'unknown'],
    country:[record.country?.trim()||'unknown'],role:[record.role?.trim()||'unknown'],year:[registrationYear(record)],
    tender:[!present('active_tender')?'unknown':Number(record.active_tender)>0?'yes':String(record.active_tender).trim()==='0'?'no':''],
    person:[present('contact_people')?'yes':'no'],site:[present('website')?'yes':'no'],
    registry:[registryMatched(record)?'geostat':'unverified'],social:[...social,social.length?'any':'none'],
    contact:[...['phone','email','website'].filter(present),...(present('phone')&&present('email')?['both']:[])],
    audience:[...(record.audiences?.includes('interior')||classification.category==='interior'?['interior']:[]),
      ...(!present('website')&&(present('phone')||present('email'))?['website']:[])]};
  value={values,search:normalize([...fields.map(f=>record[f]),record.registered_name].join(' ')),digits:[record.phone,record.fax,record.tax_id,record.source_id].flatMap(value=>String(value??'').split(';')).map(value=>value.replace(/\D/g,''))};
  cache.set(record,value);return value;
}

export function evaluateFilters(records,filters={}){
  const selected=Object.entries(filters).filter(([key,value])=>key!=='query'&&(Array.isArray(value)?value.length:!!value))
    .map(([key,value])=>[key,Array.isArray(value)?value:[value]]);
  const tokens=normalize(filters.query).split(/\s+/).filter(Boolean),counts={},rows=[];
  let categoryTotal=0;
  for(const record of records){
    const {values,search,digits}=indexed(record);
    if(!tokens.every(token=>search.includes(token)||/^[+0-9()\-]+$/.test(token)&&token.replace(/\D/g,'').length>0&&digits.some(value=>value.includes(token.replace(/\D/g,'')))))continue;
    const failed=selected.filter(([key,choices])=>!choices.some(choice=>(values[key]||[]).includes(choice))).map(([key])=>key);
    if(!failed.length)rows.push(record);
    if(!failed.length||(failed.length===1&&failed[0]==='category'))categoryTotal++;
    // Each facet counts matches with every OTHER filter applied. This retains
    // useful alternatives when several values within one facet are selected.
    if(failed.length>1)continue;
    for(const [key,options] of Object.entries(values)){
      if(failed.length&&failed[0]!==key)continue;
      const bucket=counts[key]??={};
      for(const option of options)bucket[option]=(bucket[option]||0)+1;
    }
  }
  counts.category??=Object.fromEntries(Object.keys(categories).map(key=>[key,0]));
  return {records:rows,counts,categoryTotal};
}
