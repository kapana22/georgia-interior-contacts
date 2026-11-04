// A directory can exceed V8's maximum string length. Keep each serialized
// record separate while preserving the complete JSON document and metadata.
import {createReadStream, createWriteStream} from 'node:fs';
import {rename, rm} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {Readable, Writable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import streamObject from 'stream-json/streamers/stream-object.js';

export async function readCompanySnapshot(file, {highWaterMark=64*1024}={}) {
  const entries=new Map();
  await pipeline(
    createReadStream(file,{highWaterMark}),
    streamObject.withParserAsStream(),
    new Writable({objectMode:true,write({key,value},encoding,done){entries.set(key,value);done();}})
  );
  const data=Object.fromEntries(entries);
  if(!Array.isArray(data.records))throw new TypeError('Company snapshot requires a records array');
  return data;
}

function* serialize(data) {
  yield '{';
  let first=true;
  for(const [key,value] of Object.entries(data)) {
    if(key==='records') {
      yield (first?'':',')+JSON.stringify(key)+':[';
      first=false;
      for(let i=0;i<value.length;i++) {
        if(i)yield ',';
        yield JSON.stringify(value[i])??'null';
      }
      yield ']';
    } else {
      const json=JSON.stringify(value);
      if(json===undefined)continue;
      yield (first?'':',')+JSON.stringify(key)+':'+json;
      first=false;
    }
  }
  yield '}';
}

export async function writeCompanySnapshot(file,data) {
  if(!Array.isArray(data.records))throw new TypeError('Company snapshot requires a records array');
  const temporary=file+'.tmp-'+randomUUID();
  try {
    await pipeline(Readable.from(serialize(data),{objectMode:false}),createWriteStream(temporary,{flags:'wx'}));
    await rename(temporary,file);
  } finally {
    await rm(temporary,{force:true});
  }
}
