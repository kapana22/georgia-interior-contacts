import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('developers',Path(__file__).resolve().parents[1]/'scripts/collect_developers.py')
developers=importlib.util.module_from_spec(spec);spec.loader.exec_module(developers)


def test_profile_refresh_preserves_old_facts_and_first_seen():
    url='https://saxlebi.com/developers/1'
    old={'kind':'email','value':'office@studio.ge','source_url':url,'first_seen':'2025-01-01','last_seen':'2025-01-01'}
    record={'name':'Studio','directory_profile':url,'facts':[old]}
    developers.profile('<h1>Studio</h1><section><h2>Contact information</h2><a href="https://studio.ge">Website</a></section>',record)
    assert any(f['value']=='office@studio.ge' for f in record['facts'])
    developers.profile('<h1>Studio</h1><section><h2>Contact information</h2><a href="mailto:office@studio.ge">Email</a></section>',record)
    email=next(f for f in record['facts'] if f['kind']=='email')
    assert email['first_seen']=='2025-01-01'
    assert any(f['value']=='https://studio.ge' for f in record['facts'])


def test_missing_contacts_are_distinct_from_failed_profile():
    record={'name':'Studio','directory_profile':'https://saxlebi.com/developers/1','facts':[]}
    developers.profile('<h1>Studio</h1><h2>Projects</h2>',record)
    assert record['profile_contact_status']=='contact_section_absent'
    assert record['profile_collected_at']
