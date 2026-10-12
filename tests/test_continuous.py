import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from georgia_contacts.cli import run
from georgia_contacts.discovery import discover
from georgia_contacts.network import FetchError, Deadline
from georgia_contacts.state import load_state
from georgia_contacts.campaign import source_signature

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/continue_collection.py'
spec = importlib.util.spec_from_file_location('continuation', SCRIPT)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class Client:
    requests = 0
    def __init__(self): self.calls = []
    def check(self): pass
    def fetch(self, url, **kw):
        self.calls.append(url); self.requests += 1
        return url, b'<p>Interior design studio in Tbilisi Georgia, business email info@studio.test.</p>'


def args(tmp_path, config, limit=1, discovery=False):
    sources = tmp_path / 'sources.json'
    sources.write_text(json.dumps(config))
    return SimpleNamespace(state=tmp_path/'state.json', sources=sources, output=tmp_path/'output', max_minutes=1,
                           no_discovery=not discovery, limit=limit, continuous=True)


def test_campaign_resumes_and_exhausts_without_reprocessing_success(tmp_path):
    a = args(tmp_path, {'businesses': [{'url': 'https://one.test/'}, {'url': 'https://two.test/'}]})
    c = Client(); first = run(a, c)
    assert first['work_remaining'] and first['remaining_businesses'] == 1
    c = Client(); second = run(a, c)
    assert c.calls == ['https://two.test/']
    assert second['collection_status'] == 'exhausted' and not second['work_remaining']
    c = Client(); third = run(a, c)
    assert not c.calls and third['sites_processed'] == 0


def test_failures_retry_three_batches_then_stop_without_losing_contacts(tmp_path):
    a = args(tmp_path, {'businesses': [{'url': 'https://one.test/'}]})
    run(a, Client())
    config = {'businesses': [{'url': 'https://one.test/'}, {'url': 'https://down.test/'}]}
    a = args(tmp_path, config, limit=2)
    class Failed(Client):
        def fetch(self, url, **kw):
            if 'down.test' in url: raise FetchError('unreachable', 'network_error')
            return super().fetch(url, **kw)
    for _ in range(2): assert run(a, Failed())['work_remaining']
    last = run(a, Failed())
    assert not last['work_remaining'] and last['unavailable_sources_total'] == 1
    assert load_state(a.state)['records']


def test_blocked_source_is_terminal_and_page_budget_is_deferred(tmp_path):
    a = args(tmp_path, {'businesses': [{'url': 'https://one.test/'}]})
    class Blocked(Client):
        def fetch(self,*a,**kw): raise FetchError('blocked','robots_disallowed')
    assert not run(a, Blocked())['work_remaining']
    a = args(tmp_path, {'businesses': [{'url': 'https://new.test/'}]})
    class Budget(Client):
        def fetch(self,*a,**kw): raise FetchError('skipped','page_limit')
    assert run(a, Budget())['work_remaining']
    assert not run(a, Client())['work_remaining']


def test_finished_directory_never_restarts_and_transient_error_is_bounded():
    config = {'directories': [{'adapter':'yell','enabled':True,'url':'https://www.yell.ge/companies.php?rub=151','max_pages':1,'policy_reviewed_at':'today'}]}
    progress={}; client=Client()
    discover(client,config,[],progress,one_pass=True)
    discover(client,config,[],progress,one_pass=True)
    assert len(client.calls) == 1 and not progress[config['directories'][0]['url']]['pending']
    class Failed(Client):
        def fetch(self,*a,**kw): raise FetchError('unreachable','network_error')
    progress={}
    for _ in range(3): discover(Failed(),config,[],progress,one_pass=True)
    cursor=progress[config['directories'][0]['url']]
    assert not cursor['pending'] and cursor['unavailable']


def test_discovered_candidates_checkpoint_before_a_deadline(tmp_path):
    directory='https://www.yell.ge/companies.php?rub=151'
    a=args(tmp_path, {'businesses':[], 'directories':[{'adapter':'yell','enabled':True,'url':directory,'max_pages':2,'policy_reviewed_at':'today'}]}, discovery=True)
    class Limited(Client):
        def fetch(self,url,**kw):
            if 'page=2' in url: raise Deadline()
            return url,b'<a id="SR_links_2_New" href="https://new.test/">website</a><a href="/companies.php?rub=151&page=2">2</a>'
    report=run(a,Limited())
    assert report['status']=='time_limit_partial' and report['work_remaining']
    state=load_state(a.state)
    assert state['candidates'][0]['url']=='https://new.test/'
    assert state['discovery_queue'][directory]['pending'][0].endswith('page=2')


def test_source_observation_timestamp_is_not_new_work():
    a={'url':'https://one.test/','directory_facts':[{'kind':'email','value':'info@one.test','source_url':'https://guide.test','first_seen':'old'}]}
    b=json.loads(json.dumps(a));b['directory_facts'][0]['first_seen']='new'
    assert source_signature(a)==source_signature(b)
    b['directory_facts'][0]['value']='new@one.test'
    assert source_signature(a)!=source_signature(b)


def test_controller_dispatches_only_unfinished_idle_saved_campaign():
    report={'continuous':True,'work_remaining':True,'github_run_id':'12','sources_file':'config/sources.json','batch_limit':50}
    finished=[{'id':12,'status':'completed','conclusion':'success'}]
    payload,_=controller.decision(report,finished,'12')
    assert payload['inputs']['continuous']=='true'
    assert controller.decision(report,[{'id':13,'status':'queued'}])[0] is None
    assert controller.decision(report,finished,'11')[0] is None
    assert controller.decision({**report,'work_remaining':False},finished)[0] is None
    failed=[{'id':i,'status':'completed','conclusion':'failure'} for i in [15,14,13]]
    assert controller.decision(report,failed)[0] is None


def test_stalled_batches_pause_explicitly_instead_of_infinite_dispatch(tmp_path):
    a = args(tmp_path, {'businesses': [{'url': 'https://one.test/'}]})
    class NoTime(Client):
        def fetch(self,*a,**kw): raise Deadline()
    for _ in range(2): assert run(a, NoTime())['work_remaining']
    last = run(a, NoTime())
    assert not last['work_remaining'] and last['collection_status'] == 'paused'
    assert last['remaining_businesses'] == 1
    assert last['collection_stop_reason'] == 'no_progress_requires_review'


def test_explicit_restart_crawls_again_without_deleting_records(tmp_path):
    a = args(tmp_path, {'businesses': [{'url': 'https://one.test/'}]})
    run(a, Client()); a.restart_campaign = True
    client = Client(); run(a, client)
    assert client.calls == ['https://one.test/'] and load_state(a.state)['records']


def test_target_count_does_not_stop_continuation_and_cancel_does():
    report={'continuous':True,'work_remaining':True,'github_run_id':'12','sources_file':'config/sources.json','batch_limit':1000,'target_reached':True,'records_total':1500}
    runs=[{'id':12,'status':'completed','conclusion':'success'}]
    assert controller.decision(report,runs)[0] is not None
    runs[0]['conclusion']='cancelled'
    assert controller.decision(report,runs)[0] is None


def test_worker_ignores_itself_but_never_queues_over_another_job():
    report={'continuous':True,'work_remaining':True,'github_run_id':'12','sources_file':'config/sources.json','batch_limit':1000}
    current=[{'id':12,'status':'in_progress','conclusion':None}]
    payload,_=controller.decision(report,current,'12','12')
    assert payload['inputs']['limit']=='1000'
    assert controller.decision(report,current+[{'id':13,'status':'queued'}],'12','12')[0] is None
    assert controller.decision(report,current,'11','12')[0] is None


def test_adding_a_source_preserves_finished_work_in_the_same_campaign(tmp_path):
    a=args(tmp_path, {'businesses':[{'url':'https://one.test/'}]})
    run(a,Client())
    a=args(tmp_path, {'businesses':[{'url':'https://one.test/'},{'url':'https://two.test/'}]})
    c=Client();r=run(a,c)
    assert c.calls==['https://two.test/'] and not r['work_remaining']


def test_all_waiting_sources_defer_dispatch_without_false_exhaustion(tmp_path):
    import time
    from georgia_contacts.state import empty_state, save_state
    a=args(tmp_path,{'businesses':[{'url':'https://one.test/'}]})
    state=empty_state();state['cooldowns']={'one.test':time.time()+3600};save_state(a.state,state)
    class Cooling(Client):
        def fetch(self,*a,**kw):raise FetchError('skipped','retry_after_cooldown')
    for _ in range(4):
        report=run(a,Cooling())
        assert report['work_remaining'] and report['collection_status']=='waiting'
    saved={**report,'sources_file':'config/sources.json'}
    assert controller.decision(saved,[])[0] is None
