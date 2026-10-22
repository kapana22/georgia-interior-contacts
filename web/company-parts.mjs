import {createHash} from 'node:crypto';

// Full records are retained, including evidence and import metadata.
export function companyParts(data) {
  const files=new Map(), chunks=[];
  for(let offset=50;offset<data.records.length;offset+=1000){
    const body=JSON.stringify({version:1,records:data.records.slice(offset,offset+1000)});
    const name=`part-${createHash('sha256').update(body).digest('hex')}.json`;
    files.set(name,body);chunks.push(name);
  }
  files.set('index.json',JSON.stringify({version:1,updated_at:data.updated_at,total:data.records.length,records:data.records.slice(0,50),chunks}));
  return files;
}
