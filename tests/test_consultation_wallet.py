from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.models.appointment import Appointment
from app.models.payment import Payment
from app.models.wallet import WalletMovement
from app.models.user import User
from app.models.tenant import Tenant
from app.models.company import Branch
from app.models.rbac import Role
from app.core.security import create_access_token
from app.services.paypal_service import PayPalService


def auth(who):
    return {'Authorization': 'Bearer ' + create_access_token(subject=who)}


@pytest.fixture
def setup(db_session):
    db = db_session
    for role in ['SUPERADMIN', 'ADMIN_ORGANIZATION', 'NUTRICIONISTA', 'CLIENTE']:
        db.add(Role(id=role, name=role, description=role, platform='Web'))
    db.add(Tenant(id='a', name='Empresa A', code='AA'))
    db.flush()
    db.add(Branch(id='a1', tenant_id='a', name='Sucursal', code='PRI'))
    db.flush()
    for identity, role in [('patient','CLIENTE'), ('other','CLIENTE'), ('doctor','NUTRICIONISTA'), ('admin','ADMIN_ORGANIZATION')]:
        db.add(User(id=identity, email=identity+'@example.com', full_name=identity, hashed_password='unused', role_id=role, tenant_id='a', branch_id='a1', is_active=True))
    db.flush()
    db.add(Appointment(id='appointment', patient_id='patient', nutritionist_id='doctor', tenant_id='a', branch_id='a1', scheduled_at=datetime.now(timezone.utc)+timedelta(days=1), status='PENDING'))
    db.commit()


def accept(client):
    r=client.post('/api/v1/appointments/appointment/confirm', headers=auth('admin'))
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'ACCEPTED'


def test_accept_creates_pending_charge_and_cash_confirms_once(client, db_session, setup):
    assert client.post('/api/v1/appointments/appointment/pay',headers=auth('patient'),json={'method':'WALLET'}).status_code == 409
    accept(client)
    charge=db_session.query(Payment).one()
    assert (charge.amount,charge.patient_id,charge.customer_email,charge.branch_id,charge.status)==(5,'patient','patient@example.com','a1','PENDING')
    assert client.post('/api/v1/appointments/appointment/pay',headers=auth('other'),json={'method':'PAYPAL'}).status_code==403
    assert client.post('/api/v1/appointments/appointment/pay',headers=auth('patient'),json={'method':'EFECTIVO'}).status_code==403
    for _ in range(2):
        paid=client.post('/api/v1/appointments/appointment/pay',headers=auth('admin'),json={'method':'EFECTIVO'})
        assert paid.status_code==200,paid.text
    assert db_session.query(Payment).count()==1
    assert db_session.get(Appointment,'appointment').status=='CONFIRMED'
    assert client.get('/api/v1/my-payments',headers=auth('other')).json()==[]
    assert client.get(f'/api/v1/payments/{charge.id}/pdf',headers=auth('other')).status_code==403
    assert client.get(f'/api/v1/payments/{charge.id}/pdf',headers=auth('patient')).status_code==200


def completed(amount):
    return {'status':'COMPLETED','purchase_units':[{'payments':{'captures':[{'id':'CAPTURE','status':'COMPLETED','amount':{'value':str(amount),'currency_code':'USD'}}]}}]}


def test_verified_topup_wallet_payment_and_duplicate_capture(client, db_session, setup, monkeypatch):
    monkeypatch.setattr(PayPalService,'create_order',staticmethod(lambda **kw: {'order_id':'ORDER','approval_url':'https://www.sandbox.paypal.com/checkoutnow?token=ORDER'}))
    monkeypatch.setattr(PayPalService,'get_order',staticmethod(lambda order: {'status':'APPROVED'}))
    monkeypatch.setattr(PayPalService,'capture_order',staticmethod(lambda order: {'raw':completed(10)}))
    headers={**auth('patient'),'Idempotency-Key':'topup-test-01'}
    assert client.post('/api/v1/wallet/topups',headers=headers,json={'amount':7}).status_code==422
    topup=client.post('/api/v1/wallet/topups',headers=headers,json={'amount':10})
    assert topup.status_code==200,topup.text
    assert client.get('/api/v1/wallet',headers=auth('patient')).json()['balance']==0
    for _ in range(2):
        response=client.post('/api/v1/payments/capture',headers=auth('patient'),json={'paypal_order_id':'ORDER'})
        assert response.status_code==200,response.text
    assert client.get('/api/v1/wallet',headers=auth('patient')).json()['balance']==10
    assert db_session.query(WalletMovement).count()==1
    accept(client)
    for _ in range(2):
        paid=client.post('/api/v1/appointments/appointment/pay',headers=auth('patient'),json={'method':'WALLET'})
        assert paid.status_code==200,paid.text
    assert client.get('/api/v1/wallet',headers=auth('patient')).json()['balance']==5
    assert db_session.query(WalletMovement).count()==2
    assert db_session.get(Appointment,'appointment').status=='CONFIRMED'
    stats=client.get('/api/v1/payments/stats',headers=auth('admin')).json()
    assert stats['total_collected']==5
    report=client.post('/api/v1/reports/query',headers=auth('admin'),json={'entity':'payments','aggregate':True,'metric':'sum','value_field':'amount','group_by':'branch','status':'COMPLETED'})
    assert report.status_code==200,report.text
    assert report.json()['rows'][0]['value']==5


def test_insufficient_wallet_unapproved_or_wrong_paypal_amount_never_confirms(client, db_session, setup, monkeypatch):
    accept(client)
    assert client.post('/api/v1/appointments/appointment/pay',headers=auth('patient'),json={'method':'WALLET'}).status_code==400
    monkeypatch.setattr(PayPalService,'create_order',staticmethod(lambda **kw: {'order_id':'CONSULT','approval_url':'https://www.sandbox.paypal.com/checkoutnow?token=CONSULT'}))
    r=client.post('/api/v1/appointments/appointment/pay',headers=auth('patient'),json={'method':'PAYPAL'})
    assert r.status_code==200,r.text
    monkeypatch.setattr(PayPalService,'get_order',staticmethod(lambda order: {'status':'CREATED'}))
    capture=lambda: client.post('/api/v1/payments/capture',headers=auth('patient'),json={'paypal_order_id':'CONSULT'})
    assert capture().status_code==409
    monkeypatch.setattr(PayPalService,'get_order',staticmethod(lambda order: completed(1)))
    assert capture().status_code==502
    assert db_session.get(Appointment,'appointment').status=='ACCEPTED'
    monkeypatch.setattr(PayPalService,'get_order',staticmethod(lambda order: completed(5)))
    assert capture().status_code==200
    assert capture().status_code==200
    assert db_session.get(Appointment,'appointment').status=='CONFIRMED'


def test_patient_selects_in_person_without_marking_paid_and_staff_sees_choice(client, db_session, setup):
    path='/api/v1/appointments/appointment/pay'
    assert client.post(path,headers=auth('patient'),json={'method':'PRESENCIAL'}).status_code==409
    accept(client)
    assert client.post(path,headers=auth('other'),json={'method':'PRESENCIAL'}).status_code==403
    selected=client.post(path,headers=auth('patient'),json={'method':'PRESENCIAL'})
    assert selected.status_code==200,selected.text
    assert selected.json()['status']=='PENDING'
    row=client.get('/api/v1/appointments',headers=auth('admin')).json()[0]
    assert row['payment_preference']=='PRESENCIAL'
    assert row['payment_status']=='UNPAID'
    assert row['status']=='ACCEPTED'
    assert db_session.query(WalletMovement).count()==0
    assert client.post(path,headers=auth('admin'),json={'method':'EFECTIVO'}).status_code==200
    row=client.get('/api/v1/appointments',headers=auth('patient')).json()[0]
    assert row['payment_status']=='PAID'
    assert row['payment_preference']=='PRESENCIAL'
    assert row['payment_method']=='EFECTIVO'
    assert row['status']=='CONFIRMED'
    # Completed charges cannot be reset to an unpaid preference by replaying a request.
    assert client.post(path,headers=auth('patient'),json={'method':'ONLINE'}).json()['status']=='COMPLETED'
    assert db_session.query(Payment).one().payment_method=='EFECTIVO'


def test_online_preference_is_visible_without_charging_and_legacy_is_unrecorded(client, db_session, setup):
    row=client.get('/api/v1/appointments',headers=auth('admin')).json()[0]
    assert row['payment_status']=='UNRECORDED'
    accept(client)
    response=client.post('/api/v1/appointments/appointment/pay',headers=auth('patient'),json={'method':'ONLINE'})
    assert response.status_code==200,response.text
    row=client.get('/api/v1/appointments',headers=auth('admin')).json()[0]
    assert row['payment_preference']=='ONLINE'
    assert row['payment_status']=='UNPAID'
    assert db_session.query(Payment).one().paypal_order_id is None
