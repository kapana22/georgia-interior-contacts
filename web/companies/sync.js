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
