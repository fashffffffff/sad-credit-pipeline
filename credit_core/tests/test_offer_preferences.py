from dataclasses import replace
from credit_core import Application,BkiResult,counter_offers,evaluate

def app(priority):
    return Application(app_id='app_9999',age=32,income=80_000,employment='employed',family='married',amount=500_000,term=12,amount_priority=priority)

def test_preference_changes_first_offer_without_changing_rules():
    bki=BkiResult(score=720)
    high=counter_offers(app(8),bki,'MATCH','HIGH_DTI')
    low=counter_offers(app(2),bki,'MATCH','HIGH_DTI')
    assert high[0]['amount']==500_000 and high[0]['term']>12
    assert low[0]['term']==12 and low[0]['amount']<500_000
    for o in high+low:
        assert o['total_payment']>=o['amount'] and o['overpayment']>=0
        assert o['amount_delta']==o['amount']-500_000
        assert o['term_delta']==o['term']-12
        assert evaluate(replace(app(8),amount=o['amount'],term=o['term'],product_id=o['product_id']),bki=bki,smev='MATCH').decision=='APPROVED'

def test_no_term_can_bypass_ten_income_limit():
    a=replace(app(10),income=60_000,amount=800_000,term=48)
    offers=counter_offers(a,BkiResult(score=720),'MATCH','AMOUNT_LIMIT')
    assert offers and all(o['amount']<=600_000 for o in offers)
