from datetime import datetime
import pytest
from app.models.user import User
from app.models.tenant import Tenant
from app.models.company import Branch
from app.models.rbac import Role
from app.models.payment import Payment
from app.models.appointment import Appointment
from app.models.generated_report import GeneratedReport
from app.models.activity_log import ActivityLog
from app.core.security import create_access_token
from app.services.report_intent_service import ReportIntentService
from app.services.gemini_service import GeminiService

@pytest.fixture
def reporting(db_session,monkeypatch):
    monkeypatch.setattr(GeminiService,'is_configured',lambda:False)
    monkeypatch.setattr(ReportIntentService,'today',lambda:datetime(2026,10,29).date())
    for role in ['SUPERADMIN','ADMIN_ORGANIZATION','NUTRICIONISTA','CLIENTE']:db_session.add(Role(id=role,name=role,description=role,platform='Web'))
    db_session.add_all([Tenant(id='alpha',name='Nutri Norte',code='NORTE'),Tenant(id='beta',name='Vida Sur',code='SUR'),Tenant(id='empty',name='Sin Clientes',code='VACIA')]);db_session.commit()
    for identity,company,name in [('a1','alpha','Centro'),('a2','alpha','Terminal'),('a0','alpha','Parque'),('b1','beta','Centro')]:db_session.add(Branch(id=identity,tenant_id=company,name=name,code=identity.upper()))
    db_session.commit()
    for identity,role,company,branch in [('root','SUPERADMIN',None,None),('admin','ADMIN_ORGANIZATION','alpha','a1'),('unassigned','ADMIN_ORGANIZATION',None,None),('doctor','NUTRICIONISTA','alpha','a1'),('p1','CLIENTE','alpha','a1'),('p2','CLIENTE','alpha','a1'),('p3','CLIENTE','alpha','a2'),('p4','CLIENTE','beta','b1')]:
        db_session.add(User(id=identity,full_name=identity,email=identity+'@example.com',hashed_password='unused',role_id=role,tenant_id=company,branch_id=branch,is_active=identity!='p3',created_at=datetime(2026,10,15)))
    db_session.commit()
    for identity,company,branch,amount,currency,status in [('usd1','alpha','a1',20,'USD','COMPLETED'),('usd2','alpha','a2',70,'USD','COMPLETED'),('bob1','beta','b1',500,'BOB','COMPLETED'),('pending','alpha','a1',9999,'USD','PENDING')]:
        db_session.add(Payment(id=identity,tenant_id=company,branch_id=branch,customer_name='Test',concept='Consulta',amount=amount,currency=currency,status=status,created_at=datetime(2026,10,10),paid_at=datetime(2026,10,11)))
    for identity,patient,branch,when,status in [('c1','p1','a1',datetime(2026,10,10),'COMPLETED'),('c2','p1','a1',datetime(2026,10,20),'COMPLETED'),('c3','p2','a1',datetime(2026,10,22),'COMPLETED'),('c4','p3','a2',datetime(2026,9,20),'CANCELLED')]:
        db_session.add(Appointment(id=identity,tenant_id='alpha',branch_id=branch,patient_id=patient,nutritionist_id='doctor',scheduled_at=when,status=status))
    db_session.commit()

def headers(who='root'):return {'Authorization':'Bearer '+create_access_token(subject=who)}
def voice(client,text,who='root',**scope):return client.post('/api/v1/reports/voice-command',headers=headers(who),json={'transcript':text,**scope})
def query(client,who='root',**body):return client.post('/api/v1/reports/query',headers=headers(who),json=body)

def test_company_and_branch_client_rankings_include_zero_and_real_counts(client,reporting):
    response=voice(client,'¿Qué empresa tiene más clientes?');assert response.status_code==200,response.text
    data=response.json();assert data['parsed_request']['group_by']=='company'
    rows=data['report_data']['rows'];assert [(r['company_name'],r['value']) for r in rows]==[('Nutri Norte',3),('Vida Sur',1),('Sin Clientes',0)]
    response=voice(client,'¿Qué sucursal tiene más clientes?','root',tenant_id='alpha');assert response.status_code==200,response.text
    rows=response.json()['report_data']['rows'];assert [(r['branch_name'],r['value']) for r in rows]==[('Centro',2),('Terminal',1),('Parque',0)]
    active=voice(client,'Cantidad de clientes activos por sucursal','root',tenant_id='alpha').json()['report_data'];assert sum(r['value'] for r in active['rows'])==2
    inactive=voice(client,'Cantidad de clientes inactivos por sucursal','root',tenant_id='alpha').json()['report_data'];assert sum(r['value'] for r in inactive['rows'])==1

def test_scope_names_multiselect_and_permissions(client,reporting):
    result=voice(client,'Clientes de sucursal Terminal de empresa Nutri Norte');assert result.status_code==200,result.text
    assert [r['full_name'] for r in result.json()['report_data']['rows']]==['p3']
    assert voice(client,'Clientes de sucursal Centro').status_code==400
    assert voice(client,'¿Qué empresa tiene más clientes?','admin').status_code==403
    assert voice(client,'Clientes de empresa Vida Sur','admin').status_code==403
    assert query(client,'admin',entity='patients',tenant_ids=['alpha','beta']).status_code==403
    assert query(client,'admin',entity='patients',branch_ids=['b1']).status_code==403
    assert query(client,'unassigned',entity='patients').status_code==403
    both=query(client,entity='patients',aggregate=True,group_by='branch',tenant_ids=['alpha','beta'],branch_ids=['a2','b1']);assert both.status_code==200,both.text
    assert sum(r['value'] for r in both.json()['rows'])==2

def test_income_ranking_filters_confirmed_and_does_not_mix_currencies(client,reporting):
    result=voice(client,'Ingresos cobrados por sucursal este mes en USD','root',tenant_id='alpha');assert result.status_code==200,result.text
    data=result.json();p=data['parsed_request'];assert p['metric']=='sum' and p['status']=='COMPLETED' and p['currency']=='USD'
    assert p['start_date']=='2026-10-01' and p['end_date']=='2026-10-29'
    assert [(r['branch_name'],r['value']) for r in data['report_data']['rows']]==[('Terminal',70),('Centro',20)]
    multiple=voice(client,'¿Qué empresa tiene más ingresos?');assert multiple.status_code==200,multiple.text
    data=multiple.json()['report_data'];assert 'varias monedas' in data['summary']
    assert {(r['currency'],r['value']) for r in data['rows']}=={('USD',90),('BOB',500)}

def test_unique_attended_patients_and_calendar_filters(client,reporting):
    result=voice(client,'Pacientes únicos atendidos por mes este año','admin');assert result.status_code==200,result.text
    data=result.json();assert data['parsed_request']['entity']=='appointments' and data['parsed_request']['metric']=='unique_patients'
    assert data['report_data']['rows']==[{'month':'2026-10','value':2}]
    count=voice(client,'Cantidad de citas atendidas este mes','admin');assert count.status_code==200,count.text
    assert count.json()['report_data']['rows'][0]['value']==3
    last=voice(client,'Cantidad de citas canceladas el mes pasado','root',tenant_id='alpha');assert last.status_code==200,last.text
    assert last.json()['report_data']['rows'][0]['value']==1
    assert ReportIntentService.dates('ultimos 30 dias')==('2026-09-30','2026-10-29')
    assert ReportIntentService.dates('enero a marzo de 2026')==('2026-01-01','2026-03-31')
    recent=ReportIntentService.heuristic('clientes registrados ultimos 30 dias');assert recent['limit']==200

def test_totals_before_limit_no_duplicate_patient_count(client,db_session,reporting):
    response=query(client,entity='patients',aggregate=True,group_by='company',limit=1);assert response.status_code==200,response.text
    assert response.json()['total_rows']==3 and response.json()['rows'][0]['value']==3
    assert len(response.json()['rows'])==1
    less=voice(client,'¿Qué sucursal tiene menos clientes?','root',tenant_id='alpha');assert less.status_code==200,less.text
    assert less.json()['report_data']['rows'][0]['branch_name']=='Parque'

def test_clear_failure_invalid_plan_dates_and_columns(client,reporting,monkeypatch):
    assert voice(client,'Hazme algo bonito').status_code==400
    assert voice(client,'Predice los ingresos del próximo año').status_code==400
    assert query(client,entity='payments',start_date='2026-02-30').status_code==422
    assert query(client,entity='payments',start_date='2026-12-01',end_date='2026-01-01').status_code==422
    assert query(client,entity='patients',group_by='company',metric='sum',value_field='hashed_password').status_code==400
    assert query(client,entity='patients',columns=['hashed_password']).status_code==400
    monkeypatch.setattr(GeminiService,'is_configured',lambda:True)
    monkeypatch.setattr(GeminiService,'generate_json',lambda *a,**k:{'entity':'patients','columns':['hashed_password'],'tenant_ids':['beta']})
    response=voice(client,'Listado de pacientes','admin');assert response.status_code==400

def test_ai_flexible_plan_uses_database_not_fabricated_numbers(client,reporting,monkeypatch):
    monkeypatch.setattr(GeminiService,'is_configured',lambda:True)
    monkeypatch.setattr(GeminiService,'generate_json',lambda *a,**k:{'entity':'payments','aggregate':True,'group_by':'payment_method','metric':'count','status':'COMPLETED','explanation':'Invented 999999 clients'})
    response=voice(client,'Desglosa las transacciones según cómo se abonaron','admin');assert response.status_code==200,response.text
    assert response.json()['report_data']['rows'][0]['value']==1
    assert '999999' not in response.text

def test_cross_company_save_export_and_audit_entity(client,db_session,reporting):
    body={'entity':'patients','group_by':'company','aggregate':True}
    saved=client.post('/api/v1/reports/saved',headers=headers(),json=body);assert saved.status_code==201,saved.text
    row=db_session.get(GeneratedReport,saved.json()['id']);assert row.tenant_id is None
    assert client.get('/api/v1/reports/saved/'+row.id,headers=headers('admin')).status_code in (403,404)
    for suffix in ['excel','pdf']:
        exported=client.post('/api/v1/reports/export/'+suffix,headers=headers(),json=body);assert exported.status_code==200,exported.text[:200]
        assert len(exported.content)>200
    logs=query(client,'admin',entity='activity_logs');assert logs.status_code==200,logs.text
    assert all('ip_address' not in row for row in logs.json()['rows'])


def test_cancellation_rate_numeric_filters_and_unrecognized_scope(client,reporting):
    rate=voice(client,'Tasa de cancelación de citas por sucursal este año','root',tenant_id='alpha');assert rate.status_code==200,rate.text
    data=rate.json();assert data['parsed_request']['metric']=='cancellation_rate'
    assert {r['branch_name']:r['value'] for r in data['report_data']['rows']}=={'Terminal':100,'Centro':0}
    amount=voice(client,'Cobros mayores a 30 USD','root',tenant_id='alpha');assert amount.status_code==200,amount.text
    assert {r['amount'] for r in amount.json()['report_data']['rows']}=={9999,70}
    assert voice(client,'Clientes de empresa Fantasma').status_code==400
    assert voice(client,'Clientes de sucursal Inexistente','admin').status_code==400
    assert query(client,entity='patients',filters=[{'field':'tenant_id','operator':'eq','value':'beta'}]).status_code==400


def test_dates_use_bolivian_midnight_and_preview_respects_multi_scope(client,db_session,reporting):
    db_session.add(User(id='edge',full_name='Late local',email='edge@example.com',hashed_password='unused',role_id='CLIENTE',tenant_id='alpha',branch_id='a1',created_at=datetime(2026,11,1,2,30)))
    db_session.commit()
    response=voice(client,'Cantidad de clientes registrados el 2026-10-31','admin');assert response.status_code==200,response.text
    assert response.json()['report_data']['rows'][0]['value']==1
    scoped=voice(client,'Cantidad de clientes por sucursal',tenant_ids=['alpha','beta'],branch_ids=['a2','b1']);assert scoped.status_code==200,scoped.text
    assert {r['branch_id'] for r in scoped.json()['report_data']['rows']}=={'a2','b1'}


def test_branch_attribution_cannot_be_forged_and_payment_keeps_it(client,db_session,reporting):
    body={'tenant_id':'alpha','branch_id':'b1','customer_name':'Cliente','concept':'Consulta','amount':12,'payment_method':'EFECTIVO'}
    assert client.post('/api/v1/payments/create',headers=headers('admin'),json=body).status_code==400
    body['branch_id']='a2'
    assert client.post('/api/v1/payments/create',headers=headers('admin'),json=body).status_code==400
    switched=client.put('/api/v1/users/me/workspace',headers=headers('admin'),json={'tenant_id':'alpha','branch_id':'a2'})
    assert switched.status_code==200,switched.text
    result=client.post('/api/v1/payments/create',headers=headers('admin'),json=body);assert result.status_code==200,result.text
    payment=db_session.get(Payment,result.json()['payment_id']);assert payment.branch_id=='a2'
    db_session.get(User,'admin').branch_id='a1';db_session.commit();assert db_session.get(Payment,payment.id).branch_id=='a2'


def test_manual_clinical_report_local_dates_related_names_and_exact_columns(client,db_session,reporting):
    from app.models.clinical import ClinicalRecord
    for identity, when in [('before',datetime(2026,10,31,3,59)),('first',datetime(2026,10,31,4)),('last',datetime(2026,11,1,3,59)),('after',datetime(2026,11,1,4))]:
        db_session.add(ClinicalRecord(id=identity,tenant_id='alpha',branch_id='a1',patient_id='p1',nutritionist_id='doctor',diagnosis='Control nutricional',clinical_goals='Reducir sodio',evolution_notes='Mejor descanso',created_at=when))
    db_session.commit()
    body={'entity':'clinical_records','columns':['patient_name','nutritionist_name','diagnosis','created_at'],'start_date':'2026-10-31','end_date':'2026-10-31','search':'p1'}
    response=client.post('/api/v1/reports/query',headers=headers('admin'),json=body)
    assert response.status_code==200,response.text
    report=response.json()
    assert [c['key'] for c in report['columns']]==body['columns']
    assert report['total_rows']==2
    assert {r['created_at'] for r in report['rows']}=={'2026-10-31 00:00','2026-10-31 23:59'}
    assert all(r['patient_name']=='p1' and r['nutritionist_name']=='doctor' for r in report['rows'])
    for search in ['doctor','Reducir sodio','Mejor descanso']:
        result=client.post('/api/v1/reports/query',headers=headers('admin'),json={**body,'search':search})
        assert result.status_code==200 and result.json()['total_rows']==2,result.text
    assert client.post('/api/v1/reports/query',headers=headers('admin'),json={**body,'status':'CONFIRMED'}).status_code==400
    # Exports use the same manual columns and date-filtered rows as the preview.
    from openpyxl import load_workbook
    import io
    exported=client.post('/api/v1/reports/export/excel',headers=headers('admin'),json=body)
    assert exported.status_code==200,exported.text[:200]
    rows=list(load_workbook(io.BytesIO(exported.content),data_only=True).active.values)
    labels=tuple(c['label'] for c in report['columns'])
    header=next(i for i,row in enumerate(rows) if tuple(row[:len(labels)])==labels)
    assert rows[header+1][0]=='p1' and rows[header+2][0]=='p1'
    pdf=client.post('/api/v1/reports/export/pdf',headers=headers('admin'),json=body)
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF'),pdf.text[:200]


def test_manual_report_supports_all_seven_entities_with_selected_columns(client,db_session,reporting):
    from app.models.recipe import Recipe
    from app.models.clinical import ClinicalRecord
    from app.models.ai_nutrition import NutritionalPlan
    from app.services.report_service import ReportService
    db_session.add(Recipe(id='dish',tenant_id='alpha',branch_id='a1',created_by='doctor',title='Almuerzo de prueba',ingredients='Arroz',instructions='Cocinar'))
    db_session.add(ClinicalRecord(id='record',tenant_id='alpha',branch_id='a1',patient_id='p1',nutritionist_id='doctor',diagnosis='Control'))
    db_session.add(NutritionalPlan(id='plan',tenant_id='alpha',branch_id='a1',patient_id='p1',nutritionist_id='doctor',meals_json='[]',status='APPROVED'))
    db_session.add(ActivityLog(id='mutation',tenant_id='alpha',branch_id='a1',user_id='doctor',action='CITA_CREADA',description='Registro de prueba',category='OPERACIONES'))
    db_session.commit()
    for entity, config in ReportService.ENTITIES_CONFIG.items():
        columns=[c['key'] for c in config['columns']]
        response=client.post('/api/v1/reports/query',headers=headers('admin'),json={'entity':entity,'columns':columns})
        assert response.status_code==200,(entity,response.text)
        data=response.json()
        assert data['total_rows']>0,(entity,data)
        assert [c['key'] for c in data['columns']]==columns
        assert all(set(row)==set(columns) for row in data['rows'])
        assert all(row['company_name']=='Nutri Norte' and row['branch_name']=='Centro' for row in data['rows'])
    for entity,status in [('patients','ACTIVE'),('recipes','ACTIVE'),('appointments','COMPLETED'),('payments','COMPLETED'),('nutritional_plans','APPROVED'),('activity_logs','OPERACIONES')]:
        response=client.post('/api/v1/reports/query',headers=headers('admin'),json={'entity':entity,'status':status})
        assert response.status_code==200 and response.json()['total_rows']>0,(entity,response.text)


def test_pdf_keeps_complete_clinical_text_instead_of_truncating(monkeypatch):
    import reportlab.platypus
    from app.services.report_service import ReportService
    from app.schemas.report import ReportQueryResponse, ReportColumnMeta
    original = reportlab.platypus.SimpleDocTemplate
    monkeypatch.setattr(reportlab.platypus, 'SimpleDocTemplate', lambda *args, **kwargs: original(*args, **kwargs, pageCompression=0))
    report = ReportQueryResponse(entity='clinical_records',title='Revisión clínica',generated_at=datetime(2026,10,31),columns=[ReportColumnMeta(key='diagnosis',label='Diagnóstico'),ReportColumnMeta(key='evolution_notes',label='Notas de evolución')],rows=[{'diagnosis':('Control nutricional completo. '*100)+'FIN_DIAG','evolution_notes':('Revisar evolución y seguimiento. '*100)+'FIN_NOTA'}],total_rows=1)
    content=ReportService.generate_pdf(report).getvalue()
    assert content.startswith(b'%PDF')
    assert b'FIN_DIAG' in content and b'FIN_NOTA' in content
