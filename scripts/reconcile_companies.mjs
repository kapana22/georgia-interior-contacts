import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {integrate} from './merge_companies.mjs';

const nameKey=value=>String(value||'').normalize('NFKC').toLocaleLowerCase('ka')
  .replace(/^[\s.,"„“”]+|[\s.,"„“”]+$/gu,'')
  .replace(/^(?:შ\.?\s*პ\.?\s*ს\.?|ს\.?\s*ს\.?|llc)\s*/u,'')
  .replace(/[\s.,"„“”\-+]/gu,'');

export function reconcile(records){
  const rows=[],keys=new Map();let merged=0;
  for(const record of records){
    const tax=String(record.tax_id||'').trim(), name=nameKey(record.name);
    const key=/^\d{9}$/.test(tax)&&name?tax+'|'+name:null;
    const index=key?keys.get(key):undefined;
    if(index===undefined){if(key)keys.set(key,rows.length);rows.push({...record});continue;}
    const old=rows[index];
    const next=integrate([old],[record]).records[0];
    next.source_records=[...new Map([...(next.source_records||[]),old,record].map(r=>[JSON.stringify(r),r])).values()];
    next.merged_ids=[...new Set([...(old.merged_ids||[]),...(record.merged_ids||[]),record.id])];
    next.merge_reason='same_tax_id_and_normalized_name';
    rows[index]=next;merged++;
  }
  const taxes=new Map();for(const r of rows){const t=r.tax_id?.trim();if(t)taxes.set(t,(taxes.get(t)||0)+1);}
  let conflicts=0;
  for(const row of rows){
    const conflict=(taxes.get(row.tax_id?.trim())||0)>1;
    const notes=(row.quality_notes||'').split('; ').filter(n=>n&&!n.startsWith('ერთსა და იმავე ID კოდზე'));
    if(conflict){notes.push('ერთსა და იმავე ID კოდზე განსხვავებული სახელებია — გადასამოწმებელია');conflicts++;}
    row.quality_notes=notes.join('; ');
  }
  return {records:rows,merged,conflicting_records:conflicts};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const file=process.argv[2];if(!file)throw new Error('Snapshot path required');
  const data=JSON.parse(await fs.readFile(file,'utf8')),result=reconcile(data.records);
  data.records=result.records;data.quality={merged_this_run:result.merged,conflicting_records:result.conflicting_records};
  await fs.writeFile(file+'.tmp',JSON.stringify(data));await fs.rename(file+'.tmp',file);
  console.log(JSON.stringify({merged:result.merged,conflicting_records:result.conflicting_records,total:data.records.length}));
}
