#!/usr/bin/env python3
"""Restore/save a separate data branch, never force-push or copy credentials."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

FILES = ('contacts.csv', 'contacts.json', 'changes.csv', 'report.json', 'report.txt', 'snapshot.json')


def git(*args, cwd=None, capture=False):
    return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def restore(root):
    dest = root / '.data-worktree'
    state = root / '.state/state.json'
    if dest.exists():
        raise RuntimeError('Data worktree already exists; refusing to overwrite')
    heads = git('ls-remote', '--heads', 'origin', 'refs/heads/data', capture=True).strip()
    if heads:
        git('fetch', '--no-tags', 'origin', 'refs/heads/data')
        git('worktree', 'add', '--detach', str(dest), 'FETCH_HEAD')
        if not (dest / 'state.json').is_file():
            raise RuntimeError('Existing data branch has no state.json; refusing empty reset')
        state.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dest / 'state.json', state)
    else:
        # Orphan branch must not contain source code or workflow definitions.
        git('worktree', 'add', '--detach', str(dest), 'HEAD')
        git('checkout', '--orphan', 'data-initial', cwd=dest)
        git('rm', '-rf', '.', cwd=dest)
    (root / '.state').mkdir(exist_ok=True)
    (root / '.state/restored').touch()


def save(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from georgia_contacts.state import load_state
    state = root / '.state/state.json'
    if not (root / '.state/restored').exists() or not state.is_file():
        raise RuntimeError('No successfully restored/checkpointed state; refusing push')
    load_state(state)  # Validate before changing the branch.
    dest = root / '.data-worktree'
    shutil.copyfile(state, dest / 'state.json')
    for name in FILES:
        source = root / 'output' / name
        if not source.is_file():
            raise RuntimeError('Missing result: ' + name)
        shutil.copyfile(source, dest / name)
    (dest / 'vercel.json').write_text('{"git":{"deploymentEnabled":{"data":false}}}\n', encoding='utf-8')
    git('add', '--', 'state.json', 'vercel.json', *FILES, cwd=dest)
    if git('status', '--porcelain', cwd=dest, capture=True).strip():
        git('-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com', 'commit', '-m', 'Update public contact snapshots [skip ci]', cwd=dest)
        # A concurrent/manual update causes a safe failure, never silent data loss.
        git('push', 'origin', 'HEAD:refs/heads/data', cwd=dest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['restore', 'save'])
    args = parser.parse_args()
    root = Path.cwd()
    (restore if args.action == 'restore' else save)(root)


if __name__ == '__main__':
    main()
