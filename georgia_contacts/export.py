import csv
import io
import json
from collections import Counter
from .state import atomic_text, changes_since


def safe_cell(value):
    value = str(value if value is not None else "")
    # Strip leading whitespace/control chars for detection, preserve original content.
    probe = value.lstrip("\ufeff \t\r\n\v\f\x00")
    if probe.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def write_csv(path, fields, rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(row.get(k, "")) for k in fields})
    atomic_text(path, buffer.getvalue(), encoding="utf-8-sig")


def export_all(output, state, before, report):
    output.mkdir(parents=True, exist_ok=True)
    records = state["records"]
    changes = changes_since(before, records, report["started_at"])
    rows = []
    for record in records:
        row = {"record_id": record["id"], "name": record["name"], "domains": "; ".join(record["domains"]),
               "status": record["status"], "review_reasons": "; ".join(record["review_reasons"]),
               "first_seen": record["first_seen"], "last_seen": record["last_seen"]}
        for kind, column in (("phone", "phones"), ("email", "emails"), ("website", "website"), ("instagram", "instagram"), ("facebook", "facebook"), ("city", "city"), ("phone_unverified", "phones_unverified")):
            row[column] = "; ".join(sorted({f["value"] for f in record["facts"] if f["kind"] == kind}))
        row["evidence_json"] = json.dumps(record["facts"], ensure_ascii=False, separators=(",", ":"))
        rows.append(row)
    fields = ["record_id", "name", "domains", "phones", "emails", "website", "instagram", "facebook", "city", "phones_unverified", "status", "review_reasons", "first_seen", "last_seen", "evidence_json"]
    write_csv(output / "contacts.csv", fields, rows)
    atomic_text(output / "contacts.json", json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    write_csv(output / "changes.csv", ["type", "record_id", "name", "collected_at", "before", "after"], changes)
    report["records_total"] = len(records)
    report["contacts_total"] = sum(len({(f["kind"], f["value"]) for f in r["facts"] if f["kind"] in {"phone", "email", "instagram", "facebook"}}) for r in records)
    all_direct = {(f['kind'], f['value']) for r in records for f in r['facts'] if f['kind'] in {'phone', 'email'}}
    irrelevant = {'interior_service_not_confirmed', 'georgia_operation_not_confirmed', 'demo_or_fictional_business'}
    qualified_direct = {(f['kind'], f['value']) for r in records if not irrelevant.intersection(r['review_reasons']) for f in r['facts'] if f['kind'] in {'phone', 'email'}}
    report['direct_contacts_unique'] = len(all_direct)
    report['relevant_direct_contacts_unique'] = len(qualified_direct)
    report['target_reached'] = len(qualified_direct) >= report.get('target_contacts', 1000)
    report['contacts_missing_to_target'] = max(0, report.get('target_contacts', 1000) - len(qualified_direct))

    report["new_records"] = sum(c["type"] == "new" for c in changes)
    report["changed_records"] = sum(c["type"] == "changed" for c in changes)
    report["merged_records"] = sum(c["type"] == "merged" for c in changes)
    errors = [e for e in report["events"] if e["status"] not in {"ok", "skipped"}]
    report["errors"] = len(errors)
    report["error_categories"] = dict(Counter(e["status"] for e in errors))
    atomic_text(output / "report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    text = (f"გაშვების დასაწყისი: {report['started_at']}\n"
            f"მდგომარეობა: {report['status']}\n"
            f"დამუშავებული ბიზნესის საიტები: {report['sites_processed']}\n"
            f"შენახული ბიზნესები (წინა მონაცემებით): {len(records)}\n"
            f"კონტაქტები (ტელეფონი/ელფოსტა/სოციალური): {report['contacts_total']}\n"
            f"უნიკალური პირდაპირი ტელეფონი/ელფოსტა: {report['direct_contacts_unique']}; შესაბამისი კანდიდატების: {report['relevant_direct_contacts_unique']} / {report.get('target_contacts', 1000)}\n"
            f"ახალი: {report['new_records']}; შეცვლილი: {report['changed_records']}; გაერთიანებული: {report['merged_records']}\n"
            f"შეცდომები/ბლოკირება/JS: {len(errors)}\n"
            f"კატეგორიები: {json.dumps(report['error_categories'], ensure_ascii=False)}\n"
            "დეტალური მიზეზები: report.json → events. ძველი კონტაქტები ავტომატურად არ იშლება.\n")
    atomic_text(output / "report.txt", text)
