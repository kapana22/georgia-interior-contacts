"""Count distinct contactable companies and enforce a durable collection target."""
import re

B2B_CATEGORIES = {'development','construction','architecture','interior','technology',
    'logistics','forwarding','warehousing','medical-supply','building-supply',
    'packaging','metalwork','healthcare','laboratories'}


def identity(row):
    tax = re.sub(r'\D', '', row.get('tax_id', ''))
    return 'tax:' + tax if len(tax) == 9 else 'id:' + row['id']


def contactable(row):
    email = any(re.fullmatch(r'[^\s@;]+@[^\s@;]+\.[^\s@;]+', s.strip())
                for s in re.split(r'[;,\s]+', row.get('email', '')))
    phone = any(len(re.sub(r'\D', '', s)) >= 9
                for s in re.split(r'[;,\n]+', row.get('phone', '')))
    return bool(email or phone)


def qualified(row):
    # Source-provided supplier roles qualify; name-only sector guesses do not.
    supplier = row.get('role') == 'მიმწოდებელი' and row.get('legal_status') == 'შპს'
    sectors = set(row.get('industries', [])) | {row.get('category')}
    documented_sector = bool(sectors & B2B_CATEGORIES) and bool(row.get('category_evidence'))
    return contactable(row) and (supplier or documented_sector)


def selection(records, campaign):
    baseline = set(campaign.get('baseline', []))
    unique = {}
    for row in records:
        key = identity(row)
        keys = {key, 'id:' + row['id']} | {'id:' + value for value in row.get('merged_ids', [])}
        if not qualified(row) or keys & baseline:
            continue
        # Prefer a documented sector and a website, without claiming revenue.
        registered_company = row.get('legal_status') in {'შპს','სს'} and key.startswith('tax:')
        rank = (registered_company, bool(row.get('category_evidence')), bool(row.get('website')), bool(row.get('email')), bool(row.get('phone')))
        if key not in unique or rank > unique[key][0]:
            unique[key] = (rank, row)
    ordered = sorted(unique.values(), key=lambda item: (tuple(-int(v) for v in item[0]), item[1]['id']))
    return [row for _, row in ordered[:campaign['target']]]


def progress(records, campaign):
    selected = selection(records, campaign)
    return {'target': campaign['target'], 'collected': len(selected),
            'mode': campaign['mode'], 'paused': len(selected) >= campaign['target']}
