"""Append-only contact evidence: absence is not evidence of deletion."""
import copy
import hashlib
import json
import os
from pathlib import Path


def empty_state():
    return {"schema_version": 1, "records": [], "candidates": [], "attempts": {}}


def load_state(path):
    if not Path(path).exists():
        return empty_state()
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    if state.get("schema_version") != 1 or not isinstance(state.get("records"), list):
        raise ValueError("Unsupported or damaged state; refusing to replace it")
    for record in state["records"]:
        if not all(k in record for k in ("id", "domains", "facts", "name")):
            raise ValueError("Damaged record; refusing to replace state")
    return state


def atomic_text(path, text, encoding="utf-8"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding=encoding, newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def save_state(path, state):
    atomic_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def fact_key(f):
    return f["kind"], f["value"], f["source_url"]


def union_facts(*groups):
    result = {}
    for facts in groups:
        for fact in facts:
            key = fact_key(fact)
            if key in result:
                result[key]["first_seen"] = min(result[key]["first_seen"], fact["first_seen"])
                result[key]["last_seen"] = max(result[key]["last_seen"], fact["last_seen"])
            else:
                result[key] = copy.deepcopy(fact)
    return sorted(result.values(), key=fact_key)


def identities(record):
    return {("domain", d) for d in record["domains"]} | {(f["kind"], f["value"]) for f in record["facts"] if f["kind"] in {"phone", "email"}}


def merge_record(records, incoming):
    """Merge the transitive component by exact domain, normalized phone or email."""
    incoming = copy.deepcopy(incoming)
    keys = identities(incoming)
    matches = []
    while True:
        new = [r for r in records if r not in matches and identities(r) & keys]
        if not new:
            break
        matches.extend(new)
        for r in new:
            keys |= identities(r)
    if matches:
        base = min(matches, key=lambda r: (r["first_seen"], r["id"]))
        result = copy.deepcopy(base)
        result["domains"] = sorted(set(incoming["domains"]).union(*(r["domains"] for r in matches)))
        result["facts"] = union_facts(*(r["facts"] for r in matches), incoming["facts"])
        result["last_seen"] = max(incoming["last_seen"], *(r["last_seen"] for r in matches))
        result["review_reasons"] = sorted(set(incoming["review_reasons"]).union(*(r["review_reasons"] for r in matches)))
        if incoming.get("name"):
            result["name"] = incoming["name"]
        if len(result["domains"]) > 1:
            result["review_reasons"] = sorted(set(result["review_reasons"]) | {"shared_contact_across_domains"})
        result["status"] = "review" if result["review_reasons"] else "candidate"
        records[:] = [r for r in records if r not in matches]
    else:
        result = incoming
        result["id"] = hashlib.sha256(incoming["domains"][0].encode()).hexdigest()[:16]
    records.append(result)
    records.sort(key=lambda r: r["id"])
    return result


def semantic(record):
    return {"name": record["name"], "domains": sorted(record["domains"]),
            "contacts": sorted({(f["kind"], f["value"]) for f in record["facts"]}),
            "review_reasons": sorted(record["review_reasons"])}


def changes_since(before, after, timestamp):
    old = {r["id"]: r for r in before}
    changes = []
    for r in after:
        previous = old.get(r["id"])
        if previous is None:
            changes.append({"type": "new", "record_id": r["id"], "name": r["name"], "collected_at": timestamp,
                            "before": "", "after": json.dumps(semantic(r), ensure_ascii=False)})
        elif semantic(previous) != semantic(r):
            changes.append({"type": "changed", "record_id": r["id"], "name": r["name"], "collected_at": timestamp,
                            "before": json.dumps(semantic(previous), ensure_ascii=False), "after": json.dumps(semantic(r), ensure_ascii=False)})
    # Explicitly describe absorbed identities instead of pretending they were deleted.
    for prior_id, prior in old.items():
        if not any(r["id"] == prior_id for r in after):
            target = next((r for r in after if identities(prior) & identities(r)), None)
            if target:
                changes.append({"type": "merged", "record_id": prior_id, "name": prior["name"], "collected_at": timestamp,
                                "before": prior_id, "after": target["id"]})
    return changes
