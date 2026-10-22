from georgia_contacts import b2b_campaign as campaign


def supplier(id, **extra):
    return {'id':id,'name':'Company '+id,'role':'მიმწოდებელი','legal_status':'შპს','phone':'+995599123456',**extra}


def test_only_contactable_documented_b2b_companies_count():
    assert campaign.qualified(supplier('a'))
    assert not campaign.qualified(supplier('a',phone='123'))
    assert not campaign.qualified({'id':'b','category':'construction','phone':'+995599123456'})
    assert campaign.qualified({'id':'c','category':'construction','category_evidence':'directory','email':'info@example.com'})


def test_exact_target_and_legal_entity_deduplication():
    rows=[supplier('a',tax_id='123456789'),supplier('b',tax_id='123456789'),supplier('c'),supplier('d')]
    goal={'target':2,'mode':'existing'}
    assert len(campaign.selection(rows,goal))==2
    assert campaign.progress(rows,goal)['paused']
    assert len(campaign.selection(rows,{'target':10,'mode':'existing'}))==3


def test_new_campaign_excludes_existing_company_even_after_tax_enrichment():
    rows=[supplier('a',tax_id='123456789'),supplier('b')]
    goal={'target':5000,'mode':'new','baseline':['id:a']}
    assert [row['id'] for row in campaign.selection(rows,goal)]==['b']
    assert not campaign.progress(rows,goal)['paused']


def test_registered_company_is_prioritized_over_unconfirmed_business():
    rows=[{'id':'agency','category':'technology','category_evidence':'directory','email':'info@example.com'},supplier('llc',tax_id='123456789')]
    assert campaign.selection(rows,{'target':1,'mode':'existing'})[0]['id']=='llc'
