import argparse
import copy
import fcntl
import json
import os
import subprocess
import sys
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from .discovery import discover
from .export import export_all
from .extract import extract, assess
from .network import Client, FetchError, Deadline
from .state import load_state, merge_record, save_state, union_facts
from .urls import canonical_url, domain, SOCIAL_HOSTS
from .directories import source_key, city24_profile
from .extract import RELEVANT
from .campaign import prepare, pending_businesses, finish_attempt, update_report, progress_signature


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def crawl(client, source, events):
    url = source["url"]
    if domain(url) in SOCIAL_HOSTS:
        events.append({"url": url, "status": "skipped", "reason": "social_profile_not_crawled"})
        return None
    facts = union_facts(source.get('directory_facts', []))
    texts = ['ინტერიერის დიზაინი'] if source.get('directory_service_claim') else []
    reasons = list(source.get('review_reasons', []))
    if source.get('directory_facts') or source.get('kind') == 'profile':
        reasons.append('directory_only_service_claim')
    queue, visited = ([] if source.get('kind') == 'listing' else [url]), set()
    name, any_success = source.get("name", ""), bool(facts)
    domains = [] if source.get("kind") in {"listing", "profile"} else [domain(url)]
    suggested = []
    while queue and len(visited) < 5:
        target = queue.pop(0)
        if target in visited:
            continue
        visited.add(target)
        try:
            final, html = client.fetch(target, page_limit=5)
            visited.add(final)
            if source.get('profile_adapter') == 'city24':
                parsed = city24_profile(html, final, now(), source.get('name', ''))
                suggested.extend(parsed.get('websites', []))
                domains = sorted({domain(u) for u in suggested})
                parsed['links'] = []
            else:
                parsed = extract(html, final, now(), source.get("name", ""))
                if RELEVANT.search(parsed['text']) and 'directory_only_service_claim' in reasons:
                    reasons.remove('directory_only_service_claim')
            if parsed["blocked"]:
                raise FetchError("blocked", parsed["reasons"][0])
            if not parsed["text"].strip():
                status = "javascript_required" if b"script" in html.lower() else "unreachable"
                raise FetchError(status, "no_readable_content")
            if len(parsed["text"].strip()) < 100 and not any(f["kind"] in {"phone", "email"} for f in parsed["facts"]) and b"script" in html.lower():
                raise FetchError("javascript_required", "javascript_shell_suspected")
            any_success = True
            name = name or parsed["name"]
            for fact in parsed["facts"]:
                if fact["kind"] == "website" and source.get("kind") != "profile":
                    fact["value"] = url
            facts = union_facts(facts, parsed["facts"])
            texts.append(parsed["text"])
            reasons.extend(parsed["reasons"])
            events.append({"url": final, "status": "ok", "reason": "business_page"})
            if target == url:
                # Explicit URLs are editable, but must stay on the same business host.
                queue.extend(source.get("pages", []))
            queue.extend(parsed["links"])
            queue = list(dict.fromkeys(x for x in queue if domain(x) == domain(url) and x not in visited))
            queue.sort(key=lambda x: (0 if any(k in x.lower() for k in ("contact", "kontakti", "კონტაქტ")) else 1 if any(k in x.lower() for k in ("about", "შესახებ")) else 2))
        except FetchError as exc:
            events.append({"url": target, "status": exc.status, "reason": exc.reason})
            reasons.append("page_" + exc.reason)
            # A challenge/rate limit is a stop signal, not a reason to try other URLs.
            if exc.status == "blocked":
                break
    if not any_success:
        return None
    timestamp = now()
    reasons = assess("\n".join(texts), facts, reasons)
    return {"id": "", "name": name, "domains": domains, "facts": facts,
            "business_key": source.get("business_key", ""), "suggested_websites": suggested,
            "first_seen": min((f["first_seen"] for f in facts), default=timestamp),
            "last_seen": max((f["last_seen"] for f in facts), default=timestamp),
            "status": "review" if reasons else "candidate", "review_reasons": reasons}


def load_sources(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("businesses"), list):
        raise ValueError("sources must contain a businesses list")
    for source in data["businesses"]:
        source["url"] = canonical_url(source["url"])
        if not source["url"]:
            raise ValueError("Invalid business URL")
        if len(source.get("pages", [])) > 4:
            raise ValueError("At most 4 explicit pages besides homepage")
        for page in source.get("pages", []):
            if not canonical_url(page) or domain(page) != domain(source["url"]):
                raise ValueError("Explicit pages must belong to the business")
    return data


def run(args, client=None):
    state = load_state(args.state)
    config = load_sources(args.sources)
    before = copy.deepcopy(state["records"])
    report = {"target_contacts": int(config.get("target_contacts", 1000)), "started_at": now(), "status": "running", "sites_processed": 0, "events": []}
    continuous = getattr(args, 'continuous', False)
    report['continuous'] = continuous
    report['sources_file'] = str(args.sources)
    report['batch_limit'] = args.limit
    report['github_run_id'] = os.environ.get('GITHUB_RUN_ID', '')
    if continuous:
        if getattr(args, 'restart_campaign', False):
            state.pop('campaign', None)
            state.pop('discovery_queue', None)
        prepare(state, config, args.sources, report['started_at'])
        initial_progress = progress_signature(state)
    client = client or Client(time.monotonic() + args.max_minutes * 60)

    publish_interval = getattr(args, 'publish_interval', 0)
    last_publish = time.monotonic()

    def checkpoint():
        nonlocal last_publish, publish_interval
        if continuous:
            update_report(state, config, report, no_discovery=args.no_discovery)
        report['checkpoint_at'] = now()
        run_id = report.get('github_run_id')
        if run_id:
            history = state.setdefault('run_history', [])
            history[:] = [r for r in history if r['id'] != run_id]
            history.insert(0, {'id': run_id, 'created_at': report['started_at'],
                              'status': 'in_progress' if report['status'] == 'running' else 'completed',
                              'conclusion': 'success' if report['status'] == 'completed' else 'neutral',
                              'html_url': 'https://github.com/kapana22/georgia-interior-contacts/actions/runs/' + run_id})
            del history[5:]
        state["last_run"] = report["started_at"]
        save_state(args.state, state)
        export_all(args.output, state, before, report)
        if publish_interval and os.environ.get('GITHUB_ACTIONS') == 'true' and time.monotonic() - last_publish >= publish_interval:
            try:
                subprocess.run([sys.executable, 'scripts/data_branch.py', 'save'], check=True, timeout=45)
                last_publish = time.monotonic()
            except (subprocess.SubprocessError, OSError):
                publish_interval = 0  # Stop repeated publishing failures; final workflow save remains.
                report['events'].append({'url': 'https://github.com/kapana22/georgia-interior-contacts',
                                         'status': 'error', 'reason': 'incremental_publish_failed'})

    checkpoint()
    try:
        def add_discovered(discovered):
            known = {source_key(c): c for c in state["candidates"]}
            for item in discovered:
                key = source_key(item)
                if key not in known:
                    state['candidates'].append(item)
                    known[key] = item
                else:
                    old = known[key]
                    old['directory_facts'] = union_facts(old.get('directory_facts', []), item.get('directory_facts', []))
                    old['review_reasons'] = sorted(set(old.get('review_reasons', [])) | set(item.get('review_reasons', [])))
                    for field in ('name', 'kind', 'business_key', 'directory_service_claim', 'directory_profile'):
                        if item.get(field):
                            old[field] = item[field]
            checkpoint()
        if not args.no_discovery:
            discover(client, config, report['events'], state.setdefault('discovery_queue', {}),
                     one_pass=continuous, on_candidates=add_discovered)
        sources = {}
        for source in state["candidates"] + config["businesses"]:
            key = source_key(source)
            sources[key] = {**sources.get(key, {}), **source}
        # An explicit disabled entry also excludes previously discovered candidates.
        excluded_domains = set(config.get("excluded_business_domains", []))
        candidates = [s for s in sources.values() if s.get("enabled", True) and domain(s["url"]) not in excluded_domains]
        if continuous:
            candidates = pending_businesses(state, config)
        candidates.sort(key=lambda s: (state["attempts"].get(source_key(s), ""), 0 if s.get("verified_at") else 1, domain(s["url"])))
        report["candidates_available"] = len(candidates)
        checkpoint()
        for source in candidates:
            if report["sites_processed"] >= args.limit:
                break
            client.check()
            event_start = len(report["events"])
            try:
                record = crawl(client, source, report["events"])
                if record:
                    merge_record(state["records"], record)
                    candidate_keys = {source_key(c) for c in state['candidates']}
                    for website in record.get('suggested_websites', []):
                        key = domain(website)
                        if key not in candidate_keys:
                            state['candidates'].append({'url': website, 'name': record['name'], 'discovered_from': source['url']})
                            candidate_keys.add(key)
            except Deadline:
                raise
            except Exception as exc:
                # One site's malformed markup must not discard previous checkpoints.
                report["events"].append({"url": source["url"], "status": "error", "reason": "parser_" + type(exc).__name__})
            recent = report['events'][event_start:]
            only_budget_skip = recent and all(e['status'] == 'skipped' for e in recent)
            if only_budget_skip:
                report["businesses_deferred_by_page_budget"] = report.get("businesses_deferred_by_page_budget", 0) + 1
                continue
            if continuous:
                finish_attempt(state['campaign'], source, recent)
            state['attempts'][source_key(source)] = now()
            report["sites_processed"] += 1
            checkpoint()
            print(f"Processed {report['sites_processed']}: {domain(source['url'])}", flush=True)
        report["status"] = "completed"
    except Deadline:
        report["status"] = "time_limit_partial"
    except KeyboardInterrupt:
        report["status"] = "interrupted_partial"
    finally:
        if continuous:
            update_report(state, config, report, no_discovery=args.no_discovery)
            campaign = state['campaign']
            campaign['no_progress_batches'] = campaign.get('no_progress_batches', 0) + 1 if progress_signature(state) == initial_progress else 0
            report['no_progress_batches'] = campaign['no_progress_batches']
            if report['work_remaining'] and campaign['no_progress_batches'] >= 3:
                report['collection_stop_reason'] = 'no_progress_requires_review'
        report["finished_at"] = now()
        report["http_requests"] = client.requests
        checkpoint()
    return report


def positive_limit(value):
    result = int(value)
    if not 1 <= result <= 1000:
        raise argparse.ArgumentTypeError("limit must be 1..1000")
    return result


def minutes(value):
    result = float(value)
    if not 0 < result <= 28:
        raise argparse.ArgumentTypeError("max-minutes must be >0 and <=28 (30-minute hard ceiling)")
    return result


def main():
    parser = argparse.ArgumentParser(description="Georgian interior design public business contacts")
    parser.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    parser.add_argument("--limit", type=positive_limit, default=50)
    parser.add_argument("--state", type=Path, default=Path(".state/state.json"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--max-minutes", type=minutes, default=24)
    parser.add_argument("--no-discovery", action="store_true")
    parser.add_argument("--restart-campaign", action="store_true", help="Start a fresh pass without deleting old contacts")
    parser.add_argument("--publish-interval", type=int, default=0, help="Actions-only data-branch checkpoint interval in seconds (0=disabled)")
    parser.add_argument("--continuous", action="store_true", help="Resume finite campaign; stop when configured sources are exhausted")
    args = parser.parse_args()
    if args.publish_interval and args.publish_interval < 60:
        parser.error("publish-interval must be 0 or at least 60 seconds")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    # Linux/macOS local mutual exclusion; workflow also has a repository-wide group.
    with args.state.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another process is using this state file")
        def stop(*_):
            raise Deadline()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGALRM, stop)
        signal.alarm(30 * 60)
        try:
            report = run(args)
            print((args.output / "report.txt").read_text(encoding="utf-8"))
            return 0 if report["status"] == "completed" else 2
        finally:
            signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
