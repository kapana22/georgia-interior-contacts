"""Explicit migration/verification utility; credentials come from environment."""
import argparse
import json
import os
import tempfile
from pathlib import Path
from georgia_contacts.company_store import CompanyStore, FILES, unpack

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['migrate', 'restore', 'status', 'cache-key'])
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    store = CompanyStore()
    if args.action == 'cache-key':
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write('revision='+store.checkpoint_revision()+'\n')
    elif args.action == 'status':
        print(json.dumps(store.request('GET', 'directory_meta'), ensure_ascii=False))
    elif args.action == 'migrate':
        if not args.directory:
            parser.error('--directory is required')
        if store.request('GET','collector_checkpoints',params={'id':'eq.current','select':'revision'}):
            raise RuntimeError('A durable checkpoint already exists; refusing to overwrite it with the old GitHub snapshot')
        store.save(args.directory)
        # Read the durable server copy, not the local cache, and compare all files.
        checkpoint=store.request('GET','collector_checkpoints',params={'id':'eq.current','select':'*'})[0]
        with tempfile.TemporaryDirectory() as temp:
            unpack(checkpoint,temp)
            for name in FILES:
                source=args.directory/name
                if source.exists() and source.read_bytes()!=(Path(temp)/name).read_bytes():
                    raise RuntimeError('Migration verification failed: '+name)
        print('Verified: all checkpoint files restored byte-for-byte from Supabase')
    else:
        if not args.directory or args.directory.exists():
            parser.error('--directory must be a new path')
        store.restore(args.directory)
