import uuid
from datetime import datetime, timezone
import pytest
from app.models.ai_nutrition import FoodRecord, NutritionalPlan
from app.models.patient_link import PatientNutritionistLink
from app.models.notification import Notification
from app.models.clinical import PatientAnamnesis
from app.services.ai_plan_service import AIPlanService
from tests.test_security_regressions import accounts, headers


def meal():
    return {'client_record_id': str(uuid.uuid4()), 'description': 'Pollo con arroz',
            'meal_type': 'Almuerzo', 'consumed_at': '2026-10-29T12:30:00Z',
            'calories': 450, 'protein_g': 30, 'carbs_g': 55, 'fats_g': 12}


def test_lost_response_retry_does_not_duplicate_meal(client, db_session, accounts):
    data = meal()
    first = client.post('/api/v1/food/records', headers=headers('patient'), json=data)
    retry = client.post('/api/v1/food/records', headers=headers('patient'), json=data)
    assert first.status_code == retry.status_code == 201
    assert first.json()['id'] == retry.json()['id']
    assert db_session.query(FoodRecord).count() == 1
    assert retry.json()['details']['consumed_at'].startswith('2026-10-29')
    changed = client.post('/api/v1/food/records', headers=headers('patient'), json={**data, 'calories': 900})
    assert changed.status_code == 409


def test_uuid_reused_in_another_account_is_independent(client, db_session, accounts):
    data = meal()
    a = client.post('/api/v1/food/records', headers=headers('patient'), json=data)
    b = client.post('/api/v1/food/records', headers=headers('other'), json=data)
    assert a.status_code == b.status_code == 201
    assert a.json()['id'] != b.json()['id']
    history = client.get('/api/v1/food/history', headers=headers('patient')).json()
    assert len(history) == 1 and history[0]['patient_id'] == 'patient'


@pytest.mark.parametrize('invalid', [{'calories': -1}, {'protein_g': 1001}, {'description': '   '},
                                   {'meal_type': 'invalid'}, {'patient_id': 'other'}, {'consumed_at': 'invalid'}])
def test_invalid_meal_is_rejected(client, accounts, invalid):
    assert client.post('/api/v1/food/records', headers=headers('patient'), json={**meal(), **invalid}).status_code == 422


def test_food_access_respects_clinic_and_patient_link(client, db_session, accounts):
    for actor in ['patient', 'nutri', 'org']:
        assert client.get('/api/v1/food/history?patient_id=other', headers=headers(actor)).status_code in (403, 404)
    assert client.get('/api/v1/food/history?patient_id=patient', headers=headers('nutri')).status_code == 403
    db_session.add(PatientNutritionistLink(patient_id='patient', nutritionist_id='nutri', tenant_id='a', pairing_code='NUTRI', status='LINKED'))
    db_session.commit()
    assert client.get('/api/v1/food/history?patient_id=patient', headers=headers('nutri')).status_code == 200


def plan(db, identity, status='DRAFT'):
    p = NutritionalPlan(id=identity, patient_id='patient', tenant_id='a', status=status,
                        meals_json='[{"meal_name":"Almuerzo","foods":[{"name":"Arroz","portion":"1 taza","calories":200}]}]')
    db.add(p)
    db.commit()
    return p


def test_only_professional_can_generate_and_assign(client, accounts):
    assert client.post('/api/v1/plans/generate-draft', headers=headers('patient'), json={'patient_id': 'patient'}).status_code == 403
    assert client.post('/api/v1/plans/generate-draft', headers=headers('org'), json={'patient_id': 'other'}).status_code in (403, 404)


def test_patient_sees_approved_plan_only(client, db_session, accounts):
    plan(db_session, 'draft')
    assert client.get('/api/v1/plans/my-plan', headers=headers('patient')).json() is None
    assert client.get('/api/v1/plans/patient/patient', headers=headers('patient')).json() == []
    assert client.post('/api/v1/plans/draft/approve', headers=headers('patient')).status_code == 403
    approval = client.post('/api/v1/plans/draft/approve', headers=headers('org'))
    assert approval.status_code == 200
    assert approval.json()['nutritionist_id'] == 'org'
    assert client.get('/api/v1/plans/my-plan', headers=headers('patient')).json()['id'] == 'draft'
    assert db_session.query(Notification).filter_by(user_id='patient').count() == 1
    client.post('/api/v1/plans/draft/approve', headers=headers('org'))
    assert db_session.query(Notification).filter_by(user_id='patient').count() == 1
    plan(db_session, 'next')
    assert client.put('/api/v1/plans/next', headers=headers('org'), json={'status': 'APPROVED'}).status_code == 200
    assert db_session.get(NutritionalPlan, 'draft').status == 'ARCHIVED'
    assert client.get('/api/v1/plans/my-plan', headers=headers('patient')).json()['id'] == 'next'
    db_session.autoflush = False
    updated = client.put('/api/v1/plans/next', headers=headers('org'), json={'title': 'Plan ajustado', 'status': 'APPROVED'})
    assert updated.status_code == 200
    db_session.expire_all()
    assert db_session.get(NutritionalPlan, 'next').title == 'Plan ajustado'


def test_photo_rejects_fake_image_and_cross_clinic_before_ai(client, accounts, monkeypatch):
    from app.services.gemini_service import GeminiService
    def unexpected(**kwargs):
        pytest.fail('AI must not run for rejected input')
    monkeypatch.setattr(GeminiService, 'analyze_image', unexpected)
    assert client.post('/api/v1/food/analyze', headers=headers('patient'), files={'file': ('fake.jpg', b'not an image', 'image/jpeg')}).status_code == 400
    assert client.post('/api/v1/food/analyze', headers=headers('org'), data={'patient_id': 'other'}, files={'file': ('food.jpg', b'\xff\xd8\xffabc', 'image/jpeg')}).status_code in (403, 404)


def test_staff_can_register_photo_for_authorized_patient(client, db_session, accounts, monkeypatch):
    from app.services.gemini_service import GeminiService
    monkeypatch.setattr(GeminiService, 'analyze_image', lambda **kw: {
        'alimentos': [{'nombre': 'Manzana', 'calorias': 95}], 'total': {'calorias': 95}, 'confianza': 'media'})
    response = client.post('/api/v1/food/analyze', headers=headers('org'), data={'patient_id': 'patient'},
                           files={'file': ('food.jpg', b'\xff\xd8\xffabc', 'image/jpeg')})
    assert response.status_code == 200
    record = db_session.get(FoodRecord, response.json()['record_id'])
    assert record.patient_id == 'patient'


def test_ai_draft_requires_real_ai_and_professional_assignment(client, db_session, accounts, monkeypatch):
    from app.services.gemini_service import GeminiService
    db_session.add(PatientAnamnesis(patient_id='patient', tenant_id='a', gender='M',
        weight_kg=70, height_cm=175, birth_date=datetime(1995, 1, 1), goal='Mantener peso'))
    db_session.commit()
    request = {'patient_id': 'patient', 'meals_per_day': 3, 'custom_goal': 'Mantener peso'}
    monkeypatch.setattr(GeminiService, 'is_configured', lambda: False)
    missing = client.post('/api/v1/plans/generate-draft', headers=headers('org'), json=request)
    assert missing.status_code == 400 and 'GEMINI_API_KEY' in missing.json()['detail']
    assert db_session.query(NutritionalPlan).count() == 0
    monkeypatch.setattr(GeminiService, 'is_configured', lambda: True)
    monkeypatch.setattr(GeminiService, 'generate_json', lambda prompt: {'meals': [
        {'meal_name': name, 'foods': [{'name': 'Arroz con pollo', 'portion': 'Una porción', 'calories': 650}]}
        for name in ['Desayuno', 'Almuerzo', 'Cena']]})
    draft = client.post('/api/v1/plans/generate-draft', headers=headers('org'), json=request)
    assert draft.status_code == 201
    assert draft.json()['status'] == 'DRAFT'
    assert len(draft.json()['meals']) == 3
    assert client.get('/api/v1/plans/my-plan', headers=headers('patient')).json() is None
    approved = client.post(f"/api/v1/plans/{draft.json()['id']}/approve", headers=headers('org'))
    assert approved.status_code == 200 and approved.json()['status'] == 'APPROVED'
    assert client.get('/api/v1/plans/my-plan', headers=headers('patient')).json()['id'] == draft.json()['id']
