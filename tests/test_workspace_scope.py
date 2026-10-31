from datetime import datetime, timezone
import pytest
from app.core.security import create_access_token, get_password_hash
from app.models.user import User
from app.models.tenant import Tenant
from app.models.company import Branch
from app.models.rbac import Role
from app.models.recipe import Recipe, RecipeAssignment
from app.models.payment import Payment
from app.models.activity_log import ActivityLog
from app.models.ai_nutrition import FoodRecord
from app.services.backup_recovery_service import BackupRecoveryService


def auth(who, **extra):
    return {'Authorization': 'Bearer ' + create_access_token(subject=who), **extra}


@pytest.fixture
def workspaces(db_session):
    db = db_session
    for role in ['SUPERADMIN','ADMIN_ORGANIZATION','NUTRICIONISTA','CLIENTE']:
        db.add(Role(id=role,name=role,description=role,platform='Web'))
    db.add_all([Tenant(id='a',name='Empresa A',code='A1'),Tenant(id='b',name='Empresa B',code='B1')])
    db.flush()
    for identity, company in [('a1','a'),('a2','a'),('b1','b')]:
        db.add(Branch(id=identity,tenant_id=company,name=identity,code=identity))
    db.flush()
    hashed = get_password_hash('Password123!')
    for identity, role, company, branch in [('root','SUPERADMIN',None,None),('admin','ADMIN_ORGANIZATION','a','a1'),('doctor','NUTRICIONISTA','a','a1'),('p1','CLIENTE','a','a1'),('p2','CLIENTE','a','a2'),('p3','CLIENTE','b','b1')]:
        db.add(User(id=identity,email=identity+'@example.com',full_name=identity,hashed_password=hashed,role_id=role,tenant_id=company,branch_id=branch))
    db.commit()
    for index, company, branch in [('1','a','a1'),('2','a','a2'),('3','b','b1')]:
        db.add(Recipe(id='r'+index,tenant_id=company,branch_id=branch,created_by='root',title='Receta '+index,ingredients='Arroz',instructions='Cocinar'))
        db.add(Payment(id='pay'+index,tenant_id=company,branch_id=branch,customer_name='Cliente',concept='Consulta',amount=10,status='COMPLETED'))
        db.add(ActivityLog(id='log'+index,tenant_id=company,branch_id=branch,user_id='root',action='PRUEBA_CREADA',description='Prueba',category='OPERACIONES'))
    db.commit()


def test_choices_and_workspace_changes_do_not_transfer_membership(client, db_session, workspaces):
    options = client.get('/api/v1/users/me/workspace-options',headers=auth('admin')).json()
    assert [c['id'] for c in options['companies']] == ['a']
    assert {b['id'] for b in options['branches']} == {'a1','a2'}
    assert not options['can_change_company']
    assert client.get('/api/v1/users/me/workspace-options',headers=auth('p1')).status_code == 403
    for who in ['admin','doctor']:
        assert client.put('/api/v1/users/me/workspace',headers=auth(who),json={'tenant_id':'b','branch_id':'b1'}).status_code == 403
        assert client.put('/api/v1/users/me/workspace',headers=auth(who),json={'tenant_id':'a','branch_id':'b1'}).status_code == 403
        assert client.put('/api/v1/users/me/workspace',headers=auth(who),json={'tenant_id':'a'}).status_code == 400
        changed = client.put('/api/v1/users/me/workspace',headers=auth(who),json={'tenant_id':'a','branch_id':'a2'})
        assert changed.status_code == 200, changed.text
        assert changed.json()['active_branch_id'] == 'a2'
        assert changed.json()['branch_id'] == 'a1'
        assert db_session.get(User,who).tenant_id == 'a'
    assert client.put('/api/v1/users/me/workspace',headers=auth('p1'),json={'branch_id':'a2'}).status_code == 403
    login = client.post('/api/v1/auth/login',json={'email':'admin@example.com','password':'Password123!'})
    assert login.status_code == 200
    assert login.json()['user']['active_branch_id'] == 'a2'


def test_queries_ids_and_explicit_headers_enforce_branch(client, workspaces):
    assert [r['id'] for r in client.get('/api/v1/recipes',headers=auth('admin')).json()] == ['r1']
    assert [p['id'] for p in client.get('/api/v1/users/patients',headers=auth('admin')).json()] == ['p1']
    assert client.put('/api/v1/recipes/r2',headers=auth('admin'),json={'title':'Ataque'}).status_code in (403,404)
    assert client.get('/api/v1/payments/pay2/pdf',headers=auth('admin')).status_code in (403,404)
    forged = auth('admin', **{'X-Workspace-Company':'b','X-Workspace-Branch':'b1'})
    assert client.get('/api/v1/recipes',headers=forged).status_code == 403
    second = auth('admin', **{'X-Workspace-Company':'a','X-Workspace-Branch':'a2'})
    assert [r['id'] for r in client.get('/api/v1/recipes',headers=second).json()] == ['r2']
    # A request already bound to branch A does not silently follow a later profile change.
    client.put('/api/v1/users/me/workspace',headers=auth('admin'),json={'tenant_id':'a','branch_id':'a2'})
    first = auth('admin', **{'X-Workspace-Company':'a','X-Workspace-Branch':'a1'})
    assert [r['id'] for r in client.get('/api/v1/recipes',headers=first).json()] == ['r1']
    assert [r['id'] for r in client.get('/api/v1/recipes',headers=auth('admin')).json()] == ['r2']


def test_root_global_company_and_branch_views(client, workspaces):
    assert len(client.get('/api/v1/recipes',headers=auth('root')).json()) == 3


def test_root_can_create_company_while_working_in_another_branch(client, db_session, workspaces):
    client.put('/api/v1/users/me/workspace',headers=auth('root'),json={'tenant_id':'a','branch_id':'a1'})
    candidate=client.post('/api/v1/tenants/administrator-candidates',headers=auth('root'),json={'full_name':'Nuevo Administrador','email':'newadmin@example.com','password':'Password123!'})
    assert candidate.status_code==201,candidate.text
    body={'name':'Nueva empresa Sucre','code':'NEW-SUCRE','administrator_id':candidate.json()['id'],'department_code':'01','initial_province_code':'0101','initial_municipality_code':'010101'}
    created=client.post('/api/v1/tenants',headers=auth('root'),json=body)
    assert created.status_code==201,created.text
    identity=created.json()['id']
    assert identity in {c['id'] for c in client.get('/api/v1/tenants',headers=auth('root')).json()}
    assert db_session.get(User,candidate.json()['id']).tenant_id==identity
    changed = client.put('/api/v1/users/me/workspace',headers=auth('root'),json={'tenant_id':'a','branch_id':None})
    assert changed.status_code == 200
    assert len(client.get('/api/v1/recipes',headers=auth('root')).json()) == 2
    changed = client.put('/api/v1/users/me/workspace',headers=auth('root'),json={'tenant_id':'a','branch_id':'a2'})
    assert changed.status_code == 200
    assert [r['id'] for r in client.get('/api/v1/recipes',headers=auth('root')).json()] == ['r2']
    assert client.post('/api/v1/reports/query',headers=auth('root'),json={'entity':'patients','tenant_ids':['b']}).status_code == 403
    client.put('/api/v1/users/me/workspace',headers=auth('root'),json={'tenant_id':None,'branch_id':None})
    assert len(client.get('/api/v1/recipes',headers=auth('root')).json()) == 3


def test_new_records_keep_branch_after_switch_and_foreign_patients_are_rejected(client, db_session, workspaces):
    body = {'title':'Nueva receta','calories':100,'protein':10,'carbohydrates':12,'fats':1,'ingredients':'Arroz integral','instructions':'Cocinar durante 20 minutos','assigned_patient_ids':['p1']}
    created = client.post('/api/v1/recipes',headers=auth('admin'),json=body)
    assert created.status_code == 201, created.text
    identity = created.json()['id']
    recipe = db_session.get(Recipe,identity)
    assert (recipe.tenant_id, recipe.branch_id) == ('a','a1')
    assignment = db_session.query(RecipeAssignment).filter_by(recipe_id=identity).one()
    assert (assignment.tenant_id,assignment.branch_id) == ('a','a1')
    client.put('/api/v1/users/me/workspace',headers=auth('admin'),json={'tenant_id':'a','branch_id':'a2'})
    assert client.post('/api/v1/recipes',headers=auth('admin'),json=body).status_code in (403,404)
    assert db_session.get(Recipe,identity).branch_id == 'a1'
    food = FoodRecord(patient_id='p1',image_url='test',result_json='{}')
    db_session.add(food); db_session.commit()
    assert (food.tenant_id,food.branch_id) == ('a','a1')


def test_backups_are_bound_to_branch_and_restore_cannot_cross(client, db_session, workspaces):
    response = client.post('/api/v1/backup/export',headers=auth('admin'))
    assert response.status_code == 200, response.text
    from app.services.backup_service import BackupService
    filename = response.json()['filename']
    with open(BackupService.get_backup_filepath(filename),'rb') as stream: content = stream.read()
    snapshot = BackupRecoveryService.parse(content,'a')
    assert snapshot['branch_id'] == 'a1'
    assert {r['id'] for r in snapshot['tables']['recipes']} == {'r1'}
    client.put('/api/v1/users/me/workspace',headers=auth('admin'),json={'tenant_id':'a','branch_id':'a2'})
    assert client.get('/api/v1/backup/download/'+filename,headers=auth('admin')).status_code in (403,404)
    preview = client.post('/api/v1/backup/preview',headers=auth('admin'),files={'file':('backup.json',content,'application/json')})
    assert preview.status_code == 400, preview.text
