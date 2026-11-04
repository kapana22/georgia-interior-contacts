import json
from types import SimpleNamespace

import pytest

from georgia_contacts.business_activities import apply_activities, fetch_activities
from georgia_contacts.network import FetchError


class CSVResponse:
    status_code=200
    headers={'Content-Type':'text/csv'}
    def __init__(self,body): self.body=body
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def iter_lines(self): return iter(self.body.encode().splitlines())


def csv_client(body,total=1,allowed=True):
    c=SimpleNamespace(counts={'br-api.geostat.ge':1},last={},delays={},delay=2,cooldowns={},
        clock=lambda:100,check=lambda:None,pause=lambda seconds:None,
        rules=lambda url:SimpleNamespace(can_fetch=lambda *args:allowed),
        fetch=lambda *args,**kwargs:('url',json.dumps({'pagination':{'total':total}})))
    c.session=SimpleNamespace(cookies=SimpleNamespace(clear=lambda:None),get=lambda *args,**kwargs:CSVResponse(body))
    return c


BODY='Legal_Code,Legal_Form_ID,ISActive,Full_Name,Activity_2_Code,Activity_2_Name,Personal_no,Head\n123456789,1,1,შპს ალფა,71.11.0,"არქიტექტურა,\nდიზაინი",12345678901,PRIVATE NAME\n'


def test_activity_export_keeps_only_corporate_activity_fields_and_respects_page_budget():
    client=csv_client(BODY)
    result=fetch_activities(client)
    assert result['records']['123456789']['activity']=='არქიტექტურა,\nდიზაინი'
    assert result['records']['123456789']['code']=='71.11.0'
    assert '12345678901' not in json.dumps(result) and 'PRIVATE NAME' not in json.dumps(result)
    assert client.counts['br-api.geostat.ge']==2
    localized=BODY.replace('123456789,1,1,','123456789,შეზღუდული პასუხისმგებლობის საზოგადოება,აქტიური,')
    assert fetch_activities(csv_client(localized))['records']==result['records']
    with pytest.raises(FetchError,match='robots_disallowed'):
        fetch_activities(csv_client(BODY,allowed=False))


def test_incomplete_or_wrong_legal_form_export_is_not_used():
    with pytest.raises(FetchError,match='incomplete'):
        fetch_activities(csv_client(BODY,total=2))
    with pytest.raises(FetchError,match='filter_changed'):
        fetch_activities(csv_client(BODY.replace('123456789,1,1,','123456789,30,1,')))


def test_backfill_matches_exact_tax_code_and_preserves_existing_names_contacts_and_history():
    rows=[{'id':'a','tax_id':'123456789','name':'Brand','phone':'599123456','history':[{'keep':True}]},
          {'id':'b','tax_id':'987654321','name':'Other'}]
    catalogue={'updated_at':'2026-11-04','records':{'123456789':{'name':'შპს ალფა','code':'71.11.0','activity':'არქიტექტურა'}}}
    assert apply_activities(rows,catalogue)==1
    assert rows[0]['name']=='Brand' and rows[0]['registered_name']=='შპს ალფა'
    assert rows[0]['phone']=='599123456' and rows[0]['history']==[{'keep':True}]
    assert 'activity_code' not in rows[1]
    assert apply_activities(rows,catalogue)==0
