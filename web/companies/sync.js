export async function fetchCompanySnapshot(url, fetcher=fetch) {
  const response=await fetcher(url,{cache:'no-store',signal:AbortSignal.timeout(20000)});
  if(!response.ok)throw new Error('კომპანიების ბაზა ვერ ჩაიტვირთა.');
  const data=await response.json();
  if(data.version!==1||!Array.isArray(data.records)||!data.records.length)throw new Error('ბაზის ფორმატი არასწორია.');
  return data;
}

export function overlaySavedRows(records,saved) {
  const map=new Map(records.map(r=>[r.id,r]));
  for(const row of saved){
    const base=map.get(row.id)||{};
    const merged={...base,...row};
    for(const key of ['evidence','source_records'])merged[key]=[...new Map([...(base[key]||[]),...(row[key]||[])].map(e=>[JSON.stringify(e),e])).values()];
    map.set(row.id,merged);
  }
  return [...map.values()];
}
