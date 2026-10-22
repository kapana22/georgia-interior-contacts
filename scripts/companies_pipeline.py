"""Durable company collection and publishing on an isolated data branch."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path.cwd()
TREE=ROOT/'.companies-worktree'
BRANCH='companies-data'
STATES={'developers.json':'.leads-state/developers-saxlebi.json','yell.json':'.leads-state/companies.json'}

def run(*args, cwd=ROOT, capture=False):
    return subprocess.run(args,cwd=cwd,check=True,text=True,stdout=subprocess.PIPE if capture else None).stdout

def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default

def write(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False));tmp.replace(path)

def restore():
    if TREE.exists():raise RuntimeError('Worktree already exists')
    run('git','fetch','--no-tags','origin','refs/heads/'+BRANCH)
    run('git','worktree','add','--detach',str(TREE),'FETCH_HEAD')
    for source,target in STATES.items():
        data=read(TREE/source)
        if not isinstance(data,dict):raise RuntimeError('Missing saved state: '+source)
        Path(target).parent.mkdir(exist_ok=True);shutil.copyfile(TREE/source,target)
    (ROOT/'.leads-state/companies-restored').touch()

def report():
    dev=read('.leads-state/developers-saxlebi.json',{})
    yell=read('.leads-state/companies.json',{})
    stamp=time.time()
    pending=[r for r in dev.get('records',{}).values() if not r.get('profile_collected_at')]
    dev_ready=dev.get('resume_after',0)<=stamp and max(dev.get('cooldowns',{}).values(),default=0)<=stamp
    dev_work=dev_ready and (bool(dev.get('queue')) or any(r.get('retry_after',0)<=stamp for r in pending))
    # An earlier transient error must not prevent the collector's bounded retry.
    # Parsed records can remain buffered even after the final page was fetched.
    yell_work=bool(yell.get('pending')) or (any(c.get('queue') for c in yell.get('cursors',{}).values()) and max(yell.get('cooldowns',{}).values(),default=0)<=stamp)
    old=read(TREE/'report.json',{})
    return {**old,'updated_at':datetime.now(timezone.utc).isoformat(),'automatic':True,
        'remaining_profiles':len(pending),'developer_profiles_collected':len(dev.get('records',{}))-len(pending),
        'remaining_listing_pages':len(dev.get('queue',[]))+sum(len(c.get('queue',[])) for c in yell.get('cursors',{}).values()),
        'work_ready':bool(dev_work or yell_work),'run_id':os.environ.get('GITHUB_RUN_ID',''),
        'remaining_buffered_records':len(yell.get('pending',[])),
        'source_statuses':{key:{'pages':c.get('pages',0),'remaining_pages':len(c.get('queue',[])),
            'status':'capped' if c.get('capped') else 'retry_pending' if c.get('queue') and c.get('last_error') else 'active' if c.get('queue') else 'error' if c.get('last_error') or c.get('failed_pages') else 'exhausted',
            'last_error':c.get('last_error'),'failed_pages':c.get('failed_pages',{})} for key,c in yell.get('cursors',{}).items()},
        'recent_errors':dev.get('errors',[])[-5:]}

def collect():
    if not (ROOT/'.leads-state/companies-restored').exists():raise RuntimeError('Restore first')
    stamp=time.time();previous=read(TREE/'report.json',{})
    from georgia_leads.cli import prepare,read_config
    yell=read('.leads-state/companies.json')
    config=read_config('config/companies-sources.json')
    retry_categories=[key.strip() for key in os.environ.get('RESTART_CATEGORIES','').split(',') if key.strip()]
    prepare(yell,config,restart_categories=retry_categories)
    write('.leads-state/companies.json',yell)
    catalog_refresh_due=os.environ.get('REFRESH_CATALOG')=='true' or stamp-previous.get('catalog_refresh_at',0)>86400
    if not report()['work_ready'] and not catalog_refresh_due and not retry_categories:
        print('No work due; waiting for retry or next catalogue refresh');return
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('did_work=true\n')
    dev=read('.leads-state/developers-saxlebi.json')
    yell=read('.leads-state/companies.json')
    # One blocked source must not stop another source's daily refresh.
    if not retry_categories and catalog_refresh_due:
        if not dev['queue']:
            dev['queue']=['https://saxlebi.com/developers'];dev['seen']=[]
        if not yell.get('pending') and not any(c.get('queue') for c in yell.get('cursors',{}).values()):
            prepare(yell,config,restart=True)
            previous['catalog_refresh_at']=stamp
    previous.setdefault('catalog_refresh_at',stamp)
    # Revisit collected profiles weekly, keeping prior facts until new facts arrive.
    for r in dev['records'].values():
        if r.get('profile_collected_at') and stamp-datetime.fromisoformat(r['profile_collected_at']).timestamp()>7*86400:
            r.pop('profile_collected_at',None)
    write('.leads-state/developers-saxlebi.json',dev);write('.leads-state/companies.json',yell)
    write(TREE/'report.json',previous)
    run(sys.executable,'scripts/collect_developers.py','--max-minutes','8','--profiles',os.environ.get('PROFILE_BATCH','150'))
    run(sys.executable,'-m','georgia_leads.cli','--sources','config/companies-sources.json','--state','.leads-state/companies.json','--output','output/companies-growth','--limit','1000','--max-minutes','2','--directory-only')

def save():
    if not (ROOT/'.leads-state/companies-restored').exists():raise RuntimeError('Restore first')
    # Generate a snapshot even if a process was interrupted between checkpoints.
    dev=read('.leads-state/developers-saxlebi.json')
    write('output/companies-growth/developers-saxlebi.json',{'records':list(dev['records'].values())})
    yell=read('.leads-state/companies.json')
    write('output/companies-growth/snapshot.json',{'records':yell['records']})
    run('node','scripts/merge_companies.mjs','output/companies-growth/developers-saxlebi.json','catalog',str(TREE/'data.json'))
    run('node','scripts/merge_companies.mjs','output/companies-growth/snapshot.json','general',str(TREE/'data.json'))
    for source,target in STATES.items():shutil.copyfile(target,TREE/source)
    shutil.copyfile('output/companies-growth/developers-saxlebi.json.deferred.json',TREE/'deferred.json')
    run('node','scripts/reconcile_companies.mjs',str(TREE/'data.json'))
    current=report();current['records_total']=len(read(TREE/'data.json')['records']);write(TREE/'report.json',current)
    run('git','add','--','data.json','developers.json','yell.json','report.json','deferred.json',cwd=TREE)
    if run('git','status','--porcelain',cwd=TREE,capture=True).strip():
        run('git','-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','Update company directory [skip ci]',cwd=TREE)
        run('git','push','origin','HEAD:refs/heads/'+BRANCH,cwd=TREE)
    print(json.dumps(current,ensure_ascii=False))
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:Path(summary).write_text(f"Companies: {current['records_total']}\n\nProfiles remaining: {current['remaining_profiles']}\n")

def resume():
    current=read(TREE/'report.json',{})
    if not current.get('work_ready'):print('No ready work; daily refresh/watchdog remains enabled');return
    runs=json.loads(run('gh','api','repos/kapana22/georgia-interior-contacts/actions/workflows/companies.yml/runs?per_page=5',capture=True))['workflow_runs']
    if any(str(r['id'])!=os.environ.get('GITHUB_RUN_ID') and r['status']!='completed' for r in runs):
        print('Another batch already queued');return
    run('gh','workflow','run','companies.yml','--ref','main')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['restore','collect','save','resume']);args=p.parse_args()
    globals()[args.action]()
