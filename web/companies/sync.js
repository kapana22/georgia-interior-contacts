export async function fetchCompanySnapshot(url, fetcher=fetch) {
  const response=await fetcher(url,{cache:'no-store',signal:AbortSignal.timeout(20000)});
  if(!response.ok)throw new Error('კომპანიების ბაზა ვერ ჩაიტვირთა.');
  const data=await response.json();
  if(data.version!==1||!Array.isArray(data.records)||!data.records.length)throw new Error('ბაზის ფორმატი არასწორია.');
  return data;
}

export async function loadCompanyParts(onProgress,fetcher=fetch) {
  const base='/companies/parts/';
  const index=await fetchCompanySnapshot(base+'index.json',fetcher);
  if(!Number.isInteger(index.total)||index.total<index.records.length||!Array.isArray(index.chunks)||index.chunks.some(name=>!/^part-[a-f0-9]{64}\.json$/.test(name)))throw new Error('ბაზის ნაწილების სია არასწორია.');
  const data={version:1,updated_at:index.updated_at,records:[...index.records]};
  await onProgress(data,index.total);
  for(const name of index.chunks){
    const part=await fetchCompanySnapshot(base+name,fetcher);
    data.records.push(...part.records);
    if(data.records.length>index.total)throw new Error('ბაზის ზომა არ ემთხვევა.');
    await onProgress(data,index.total);
  }
  if(data.records.length!==index.total)throw new Error('ბაზა არასრულად ჩაიტვირთა.');
  return data;
}

export function createLiveLoader(fetcher=fetch) {
  let sha, snapshot;
  return async()=>{
    const response=await fetcher('https://api.github.com/repos/kapana22/georgia-interior-contacts/git/ref/heads/companies-data',
      {cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('განახლების შემოწმება დროებით მიუწვდომელია.');
    const next=(await response.json()).object?.sha;
    if(!/^[a-f0-9]{40}$/.test(next||''))throw new Error('მონაცემების ვერსია არასწორია.');
    if(next!==sha){
      const data=await fetchCompanySnapshot(`https://raw.githubusercontent.com/kapana22/georgia-interior-contacts/${next}/data.json`,fetcher);
      snapshot=data;sha=next;
    }
    return snapshot;
  };
}

export function overlaySavedRows(records,saved) {
  const map=new Map(records.map(r=>[r.id,r]));
  const aliases=new Map(records.flatMap(r=>(r.merged_ids||[]).map(id=>[id,r.id])));
  for(const row of saved){
    const id=aliases.get(row.id)||row.id;
    const base=map.get(id)||{};
    const merged={...base,...row,id};
    for(const key of ['evidence','source_records'])merged[key]=[...new Map([...(base[key]||[]),...(row[key]||[])].map(e=>[JSON.stringify(e),e])).values()];
    map.set(id,merged);
  }
  return [...map.values()];
}

export function createDatabaseLoader(config, fetcher=fetch) {
  let revision, snapshot;
  async function query(table,params) {
    const response=await fetcher(`${config.url}/rest/v1/${table}?${new URLSearchParams(params)}`,{
      headers:{apikey:config.key},credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000)
    });
    if(!response.ok)throw new Error('ბაზასთან კავშირი დროებით ვერ მოხერხდა.');
    return response.json();
  }
  async function metadata(){
    const rows=await query('directory_meta',{id:'eq.current',select:'status,revision,total,updated_at'});
    const meta=rows[0];
    if(!meta||meta.status!=='ready'||!Number.isInteger(meta.total)||meta.total<1)throw new Error('ბაზა ახლდება; ცოტა ხანში ხელახლა ვცდით.');
    return meta;
  }
  return async(onProgress)=>{
    const meta=await metadata();
    if(revision===meta.revision&&snapshot)return snapshot;
    const records=[],seen=new Set();let cursor;
    while(records.length<meta.total){
      const params={select:'id,record',active:'eq.true',order:'id.asc',limit:records.length?1000:50};
      if(cursor!==undefined)params.id='gt.'+cursor;
      const batch=await query('companies',params);
      if(!Array.isArray(batch)||!batch.length)throw new Error('ბაზა არასრულად ჩაიტვირთა.');
      for(const row of batch){
        if(!row.id||row.record?.id!==row.id||seen.has(row.id))throw new Error('ბაზის ფორმატი არასწორია.');
        seen.add(row.id);records.push(row.record);
      }
      if(records.length>meta.total)throw new Error('ბაზა ჩატვირთვისას შეიცვალა.');
      cursor=batch.at(-1).id;
      if(onProgress)await onProgress({version:1,updated_at:meta.updated_at,records:[...records]},meta.total);
    }
    const final=await metadata();
    if(final.revision!==meta.revision||final.total!==meta.total)throw new Error('ბაზა ჩატვირთვისას შეიცვალა; ხელახლა ვცდით.');
    snapshot={version:1,updated_at:meta.updated_at,records};revision=meta.revision;
    return snapshot;
  };
}
