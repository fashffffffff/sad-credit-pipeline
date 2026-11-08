import pytest
from backend.tests.conftest import good_form

async def quote(env, client, **kw):
    form={k:v for k,v in good_form().items() if k!='passport_hash'} | kw
    return await env.client.post('/api/v1/quote',json=form,headers=client)

async def test_screenshot_product_mismatch_keeps_real_numbers(env,client):
    r=await quote(env,client,amount=5_000_000,income=3_430_000,term=3,product_id='prod_premium')
    assert r.status_code==200
    q=r.json()
    assert q['monthly_payment']>1_600_000 and q['dti']>0
    assert q['total_payment']>5_000_000 and q['overpayment']==q['total_payment']-5_000_000
    assert q['status']=='product_mismatch' and '12' in q['fields']['product_id']
    assert any(p['product_id']=='prod_premium' for p in q['eligible_products'])
    assert q['recommendations'][0]['amount']==5_000_000 and q['recommendations'][0]['term']>=12

async def test_quote_profile_unknown_has_no_fictional_score(env,client):
    q=(await quote(env,client,profile_complete=False)).json()
    assert q['monthly_payment']>0 and q['partial_score'] is None

async def test_missing_fields_are_not_refusal_or_zero_payment(env,client):
    q=(await quote(env,client,income=None)).json()
    assert q['status']=='needs_data' and q['monthly_payment'] is None
    assert q['dti'] is None and q['fields']['income']

@pytest.mark.parametrize('priority',[-1,11,2.5,True,'8'])
async def test_priority_must_be_integer_in_range(env,client,priority):
    assert (await quote(env,client,amount_priority=priority)).status_code==422

async def test_priority_is_saved_for_later_offers(env,client):
    r=await env.submit(good_form(amount_priority=2),client)
    assert r.status_code==201
    await env.drain()
    row=env.state.db.query_one('SELECT amount_priority FROM applications WHERE app_id=?',(r.json()['app_id'],))
    assert row['amount_priority']==2


def test_existing_database_gets_priority_without_losing_applications(tmp_path):
    import sqlite3
    from backend.app.db import Database, SCHEMA
    path=str(tmp_path/'old.sqlite')
    old=sqlite3.connect(path)
    old.executescript(SCHEMA.replace('    amount_priority INTEGER NOT NULL DEFAULT 8 CHECK(amount_priority BETWEEN 0 AND 10),\n',''))
    old.execute("INSERT INTO applications(app_id,client_id,passport_hash,product_id,channel,status,created_at) VALUES(?,?,?,?,?,?,?)",
                ('app_0001','cli_0001','0123456789abcdef','prod_standard','online','REFUSED','2026-11-03'))
    old.commit();old.close()
    migrated=Database(path,bcrypt_rounds=4)
    row=migrated.query_one('SELECT app_id,amount_priority FROM applications')
    assert row['app_id']=='app_0001' and row['amount_priority']==8
    migrated.conn.close()


async def test_unknown_age_does_not_falsely_reject_age_limited_product(env,client):
    # экспресс — до 60 лет; пока дата рождения не введена, возраст не выдумываем и продукт не отклоняем
    q=(await quote(env,client,amount=100_000,term=12,product_id='prod_express',age=65,age_known=False,profile_complete=False)).json()
    assert q['status']=='ready' and q['partial_score'] is None
    assert 'product_id' not in q['fields']
    assert 'prod_express' in {p['product_id'] for p in q['eligible_products']}
