"""Exercise the actual data-branch scripts against an offline bare Git remote."""
import os
from pathlib import Path
import subprocess
import sys
import json

from georgia_contacts.state import empty_state, save_state

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/data_branch.py'
PACKAGE = str(SCRIPT.parent.parent)


def test_data_branch_roundtrip_and_no_workflow_files(tmp_path):
    env = dict(os.environ, PYTHONPATH=PACKAGE, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    def call(*args, cwd=tmp_path):
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout
    remote=tmp_path/'remote.git'
    repo=tmp_path/'repo'
    repo.mkdir()
    call('git','init','--bare',str(remote))
    call('git','init','-b','main',cwd=repo)
    call('git','config','user.name','Test',cwd=repo)
    call('git','config','user.email','test@example.invalid',cwd=repo)
    (repo/'README.md').write_text('test')
    call('git','add','README.md',cwd=repo)
    call('git','commit','-m','initial',cwd=repo)
    call('git','remote','add','origin',str(remote),cwd=repo)
    call('git','push','-u','origin','main',cwd=repo)
    call(sys.executable,str(SCRIPT),'restore',cwd=repo)
    state=empty_state()
    save_state(repo/'.state/state.json',state)
    output=repo/'output'; output.mkdir()
    for name in ('contacts.csv','contacts.json','changes.csv','report.json','report.txt'):
        (output/name).write_text('[]')
    call(sys.executable,str(SCRIPT),'save',cwd=repo)
    files=call('git','--git-dir',str(remote),'ls-tree','--name-only','data').splitlines()
    assert 'state.json' in files and '.github' not in files and 'README.md' not in files
    clone=tmp_path/'next'
    call('git','clone','--branch','main',str(remote),str(clone))
    call(sys.executable,str(SCRIPT),'restore',cwd=clone)
    assert json.loads((clone/'.state/state.json').read_text())==state
