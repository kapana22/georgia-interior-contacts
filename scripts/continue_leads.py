#!/usr/bin/env python3
"""Dispatch only unfinished campaigns, using the built-in token and fixed workflow."""
import json
import os
import re
import time
import urllib.request

REPOSITORY = 'kapana22/georgia-interior-contacts'
API = 'https://api.github.com/repos/' + REPOSITORY


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Unexpected redirect; token was not forwarded')


def decision(report, runs, triggering_run='', current_run=''):
    if report.get('status') in {'failed', 'interrupted'}:
        return None, 'Failed or interrupted campaign requires manual review'
    runs = [r for r in runs if str(r['id']) != str(current_run)]
    if report.get('pipeline') != 'b2b-leads' or not report.get('continuous') or not report.get('work_remaining'):
        return None, 'No unfinished continuous campaign'
    if report.get('resume_after', 0) > time.time():
        return None, 'Waiting for source Retry-After; watchdog will resume later'
    if any(r['status'] != 'completed' for r in runs):
        return None, 'Collection already running or queued'
    if not current_run and runs and runs[0].get('conclusion') == 'cancelled':
        return None, 'Last collection cancelled; manual start required'
    if triggering_run and str(report.get('github_run_id')) != str(triggering_run):
        return None, 'Completed run does not own the current checkpoint'
    if not current_run and len(runs) >= 3 and all(r.get('conclusion') != 'success' for r in runs[:3]) and str(report.get('github_run_id')) not in {str(r['id']) for r in runs[:3]}:
        return None, 'Three failed runs without a saved checkpoint; manual repair required'
    sources = report.get('sources_file', '')
    limit = report.get('batch_limit', 50)
    if not re.fullmatch(r'config/[A-Za-z0-9_/-]+\.json', sources) or '..' in sources or '//' in sources:
        raise ValueError('Invalid saved source path')
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('Invalid saved batch limit')
    return {'ref': 'main', 'inputs': {'limit': str(limit), 'sources': sources, 'continuous': 'true'}}, 'Continue remaining work'


def main():
    token = os.environ['GH_TOKEN']
    opener = urllib.request.build_opener(NoRedirect())
    def request(url, body=None, authenticated=True):
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'GeorgiaBusinessLeads-Continuation'}
        if authenticated:
            if not url.startswith(API + '/'):
                raise ValueError('Unsupported authenticated endpoint')
            headers['Authorization'] = 'Bearer ' + token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with opener.open(req, timeout=20) as response:
            data = response.read()
            return json.loads(data) if data else {}
    ref = request(API + '/git/ref/heads/leads-data')['object']['sha']
    if not re.fullmatch(r'[a-f0-9]{40}', ref):
        raise ValueError('Invalid data reference')
    report = request(f'https://raw.githubusercontent.com/{REPOSITORY}/{ref}/report.json', authenticated=False)
    runs = request(API + '/actions/workflows/leads.yml/runs?per_page=5&branch=main')['workflow_runs']
    payload, reason = decision(report, runs, os.environ.get('TRIGGERING_RUN_ID', ''), os.environ.get('CURRENT_RUN_ID', ''))
    print(reason)
    if payload:
        # Exactly one dispatch attempt; a timeout never repeats the POST.
        request(API + '/actions/workflows/leads.yml/dispatches', payload)
        print('Next batch requested')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write(reason + '\n')


if __name__ == '__main__':
    main()
