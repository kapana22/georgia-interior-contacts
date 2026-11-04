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

function validateSnapshot(data,meta){
  if(data?.version!==1||!Array.isArray(data.records)||data.records.length!==meta.total)throw new Error('ბაზა არასრულად ჩაიტვირთა.');
  const ids=new Set();
  for(const row of data.records){if(!row?.id||ids.has(row.id))throw new Error('ბაზის ფორმატი არასწორია.');ids.add(row.id);}
  return {...data,revision:meta.revision};
}
function validManifest(meta){
  const value=meta.snapshot;
  return value&&/^[a-f0-9]{64}$/.test(value.sha256)&&value.path===`snapshots/${value.sha256}.json.gz`&&value.total===meta.total&&Number.isInteger(value.bytes)&&value.bytes>0&&value.bytes<=50*1024*1024;
}
async function decodeSnapshot(bytes,meta){
  if(!validManifest(meta)||bytes.byteLength!==meta.snapshot.bytes)throw new Error('ბაზის ასლი არასრულია.');
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
  if(digest!==meta.snapshot.sha256)throw new Error('ბაზის ასლი დაზიანებულია.');
  const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return validateSnapshot(await new Response(stream).json(),meta);
}

export function createDatabaseLoader(config, fetcher=fetch, {cache}={}) {
  let revision, snapshot, cacheRead=false;
  async function query(table,params) {
    const response=await fetcher(`${config.url}/rest/v1/${table}?${new URLSearchParams(params)}`,{
      headers:{apikey:config.key},credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000)
    });
    if(!response.ok)throw new Error('ბაზასთან კავშირი დროებით ვერ მოხერხდა.');
    return response.json();
  }
  async function metadata(){
    const rows=await query('directory_meta',{id:'eq.current',select:'status,revision,total,updated_at,snapshot:report->ui_snapshot'});
    const meta=rows[0];
    if(!meta||meta.status!=='ready'||!Number.isInteger(meta.total)||meta.total<1||typeof meta.revision!=='string')throw new Error('ბაზა ახლდება; ცოტა ხანში ხელახლა ვცდით.');
    return meta;
  }
  async function download(meta,onProgress){
    const response=await fetcher(`${config.url}/storage/v1/object/public/company-directory/${meta.snapshot.path}`,{
      credentials:'omit',cache:'force-cache',redirect:'error',signal:AbortSignal.timeout(120000)
    });
    if(!response.ok)throw new Error('ბაზის ასლი ვერ ჩაიტვირთა.');
    const reader=response.body.getReader(),chunks=[];let loaded=0,last=0;
    while(true){
      const {done,value}=await reader.read();if(done)break;
      loaded+=value.length;
      if(loaded>meta.snapshot.bytes){await reader.cancel();throw new Error('ბაზის ასლის ზომა არ ემთხვევა.');}
      chunks.push(value);
      if(onProgress&&(Date.now()-last>150||loaded===meta.snapshot.bytes)){
        last=Date.now();await onProgress({records:[],loadedBytes:loaded,totalBytes:meta.snapshot.bytes},meta.total);
      }
    }
    const bytes=new Uint8Array(loaded);let offset=0;
    for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    return bytes;
  }
  async function paged(meta,onProgress){
    const records=[],seen=new Set();
    const append=batch=>{
      for(const row of batch){
        if(!row.id||row.record?.id!==row.id||seen.has(row.id))throw new Error('ბაზის ფორმატი არასწორია.');
        seen.add(row.id);records.push(row.record);
      }
      if(records.length>meta.total)throw new Error('ბაზა ჩატვირთვისას შეიცვალა.');
    };
    const first=await query('companies',{select:'id,record',active:'eq.true',order:'id.asc',limit:Math.min(50,meta.total)});
    if(first.length!==Math.min(50,meta.total))throw new Error('ბაზა არასრულად ჩაიტვირთა.');
    append(first);
    if(onProgress)await onProgress({records:[...records],loaded:records.length},meta.total);
    let next=50,failed=false;const batches=new Map();
    await Promise.all(Array.from({length:4},async()=>{
      while(next<meta.total&&!failed){
        const offset=next;next+=1000;
        try{
          const expected=Math.min(1000,meta.total-offset);
          const batch=await query('companies',{select:'id,record',active:'eq.true',order:'id.asc',limit:expected,offset});
          if(batch.length!==expected)throw new Error('ბაზა არასრულად ჩაიტვირთა.');
          append(batch);batches.set(offset,batch);
          if(onProgress)await onProgress({records:first.map(row=>row.record),loaded:records.length},meta.total);
        }catch(error){failed=true;throw error;}
      }
    }));
    const ordered=[...first,...[...batches].sort((a,b)=>a[0]-b[0]).flatMap(([,batch])=>batch)].map(row=>row.record);
    return validateSnapshot({version:1,updated_at:meta.updated_at,records:ordered},meta);
  }
  return async(onProgress,onCached)=>{
    if(!cacheRead){
      cacheRead=true;
      try{
        const saved=await cache?.read();
        if(saved){snapshot=await decodeSnapshot(saved.bytes,saved.meta);revision=saved.meta.revision;if(onCached)await onCached(snapshot);}
      }catch{snapshot=undefined;revision=undefined;}
    }
    const meta=await metadata();
    if(revision===meta.revision&&snapshot)return snapshot;
    let data,bytes;
    if(validManifest(meta)&&typeof DecompressionStream!=='undefined'){
      bytes=await download(meta,onProgress);data=await decodeSnapshot(bytes,meta);
    }else data=await paged(meta,onProgress);
    const final=await metadata();
    if(final.revision!==meta.revision||final.total!==meta.total)throw new Error('ბაზა ჩატვირთვისას შეიცვალა; ხელახლა ვცდით.');
    snapshot=data;revision=meta.revision;
    if(bytes&&cache){try{await cache.write({meta,bytes});}catch{/* Browsing works when storage is full/unavailable. */}}
    return snapshot;
  };
}
