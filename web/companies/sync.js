export async function fetchCompanySnapshot(url, fetcher=fetch) {
  const response=await fetcher(url,{cache:'no-store',signal:AbortSignal.timeout(20000)});
  if(!response.ok)throw new Error('კომპანიების ბაზა ვერ ჩაიტვირთა.');
  const data=await response.json();
  if(data.version!==1||!Array.isArray(data.records)||!data.records.length)throw new Error('ბაზის ფორმატი არასწორია.');
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
