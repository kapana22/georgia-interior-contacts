// Merge public scraper exports into the unified, deployable company directory.
// Re-running an input is idempotent. Names alone never merge legal entities.
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {normalizeImport,mergeRecords,categories} from '../web/companies/core.js';

const normalizedName=s=>String(s||'').normalize('NFKC').toLocaleLowerCase('ka').replace(/^(შპს|სს|llc)\s+/u,'').replace(/[\s"„“”.,-]/gu,'');
function contacts(r){return ['email','website','phone'].flatMap(k=>String(r[k]||'').split(/[;,\n]/).map(v=>k==='phone'?v.replace(/\D/g,'').replace(/^995/,''):v.trim().toLowerCase().replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,'')).filter(v=>v.length>=5).map(v=>k+':'+v));}
export function integrate(base, incoming, {deferNameOnly=false}={}){
  const result=base.map(r=>({...r})),ids=new Map(),taxes=new Map(),matches=new Map(),names=new Set();
  function index(r,i){names.add(normalizedName(r.name));ids.set(r.id,i);for(const source of r.source_records||[])if(source.id)ids.set(String(source.id),i);if(r.tax_id?.trim()){const k=r.tax_id.trim();taxes.set(k,taxes.has(k)&&taxes.get(k)!==i?null:i);}for(const c of contacts(r)){const k=normalizedName(r.name)+'|'+c;matches.set(k,matches.has(k)&&matches.get(k)!==i?null:i);}}
  result.forEach(index);let added=0,updated=0;const deferred=[];
  for(let r of incoming){
    let target=ids.get(r.id);if(target===undefined&&r.tax_id?.trim())target=taxes.get(r.tax_id.trim());
    if(target===undefined){
      const possible=new Set(contacts(r).map(c=>matches.get(normalizedName(r.name)+'|'+c)).filter(i=>i!==undefined&&i!==null));
      const conflicts=[...possible].filter(i=>r.tax_id&&result[i].tax_id&&r.tax_id.trim()!==result[i].tax_id.trim());
      for(const i of conflicts)possible.delete(i);
      if(possible.size===1)target=[...possible][0];
      else if(conflicts.length)r={...r,quality_notes:[r.quality_notes,'იგივე სახელი/კონტაქტი სხვა ID კოდთანაც გვხვდება: '+conflicts.map(i=>result[i].tax_id).join(', ')+'. გაერთიანება საჭიროებს გადამოწმებას.'].filter(Boolean).join('\n')};
    }
    if((target===undefined||target===null)&&deferNameOnly&&names.has(normalizedName(r.name))){deferred.push(r);continue;}
    if(target===undefined||target===null){index(r,result.length);result.push(r);added++;continue;}
    const old=result[target];if(old.tax_id&&r.tax_id&&old.tax_id!==r.tax_id)throw new Error('Conflicting tax IDs: '+r.id);
    const merged=mergeRecords([old],[{...r,id:old.id}])[0];
    for(const field of ['phone','phone_unverified','email','website','facebook','instagram','linkedin','youtube','tiktok','social_links'])merged[field]=[...new Set([old[field],r[field]].filter(Boolean).flatMap(v=>v.split(/;\s*/)))].join('; ');
    for(const field of ['name','tax_id','legal_status','legal_status_full','registered_at','source_id'])merged[field]=old[field]||r[field]||'';
    merged.audiences=[...new Set([...(old.audiences||[]),...(r.audiences||[])])];
    merged.industries=[...new Set([...(old.industries||[]),...(r.industries||[]),old.category,r.category].filter(c=>categories[c]&&c!=='unknown'))];
    result[target]=merged;index(merged,target);ids.set(r.id,target);updated++;
  }
  return {records:result,added,updated,deferred};
}

async function main(){
  const [file,mode='general',destination='web/companies/data.json']=process.argv.slice(2);
  if(!file)throw new Error('Usage: node scripts/merge_companies.mjs snapshot.json [interior|general|catalog] [destination]');
  const payload=JSON.parse(await fs.readFile(file,'utf8')),rows=Array.isArray(payload)?payload:payload.records;
  const sourceRows=rows.map(r=>{const industries=(r.categories||[]).filter(c=>categories[c]);if(mode==='interior')industries.push('interior');return {...r,source:r.source||'public-business-directory',category:industries.includes('development')?'development':industries[0],industries,audiences:mode==='interior'?['interior']:[],classification_status:'candidate'};});
  const incoming=normalizeImport(sourceRows),data=JSON.parse(await fs.readFile(destination,'utf8'));
  const result=integrate(data.records,incoming,{deferNameOnly:mode==='catalog'});data.records=result.records;data.updated_at=new Date().toISOString();
  await fs.writeFile(destination+'.tmp',JSON.stringify(data));await fs.rename(destination+'.tmp',destination);
  if(mode==='catalog')await fs.writeFile(file+'.deferred.json',JSON.stringify({records:result.deferred}));
  console.log(JSON.stringify({input:incoming.length,added:result.added,updated:result.updated,deferred:result.deferred.length,total:result.records.length}));
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)await main();
