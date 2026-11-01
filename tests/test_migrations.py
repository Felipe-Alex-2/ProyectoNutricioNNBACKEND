import os
from pathlib import Path
import subprocess
import sys
from sqlalchemy import create_engine, inspect, text
from sqlalchemy import MetaData
from app.database import Base
import app.models


def test_workspace_upgrade_preserves_history_and_does_not_guess_multi_branch(tmp_path):
    database = tmp_path / 'before_workspace.sqlite'
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    legacy = MetaData()
    for table in Base.metadata.sorted_tables: table.to_metadata(legacy)
    new_tenants = {'chat_messages','food_records','recipe_assignments','subscription_receipts'}
    operational = {'activity_logs','nutritional_plans','chat_messages','food_records','backup_settings','backup_logs','patient_anamnesis','clinical_records','generated_reports','notifications','patient_nutritionist_links','recipes','recipe_assignments','subscriptions','subscription_receipts'}
    for name, table in legacy.tables.items():
        remove = {'workspace_tenant_id','workspace_branch_id'} if name == 'users' else ({'branch_id'} if name in operational else set())
        if name in new_tenants: remove.add('tenant_id')
        for field in remove:
            column = table.c[field]
            for index in list(table.indexes):
                if field in index.columns: table.indexes.remove(index)
            for constraint in list(table.constraints):
                if field in constraint.columns: table.constraints.remove(constraint)
            for fk in list(column.foreign_keys): table.foreign_keys.discard(fk)
            table._columns.remove(column)
    legacy.create_all(engine)
    with engine.begin() as c:
        c.execute(legacy.tables['roles'].insert(),{'id':'CLIENTE','name':'Cliente','description':'Cliente','platform':'Mobile'})
        c.execute(legacy.tables['tenants'].insert(),[{'id':'single','name':'Single','code':'ONE'},{'id':'multi','name':'Multi','code':'TWO'}])
        c.execute(legacy.tables['branches'].insert(),[{'id':'s1','tenant_id':'single','name':'Uno','code':'1'},{'id':'m1','tenant_id':'multi','name':'Uno','code':'1'},{'id':'m2','tenant_id':'multi','name':'Dos','code':'2'}])
        c.execute(legacy.tables['users'].insert(),[{'id':'u1','email':'u1@example.com','full_name':'Uno','hashed_password':'hash','role_id':'CLIENTE','tenant_id':'single','branch_id':'s1'},{'id':'u2','email':'u2@example.com','full_name':'Dos','hashed_password':'hash','role_id':'CLIENTE','tenant_id':'multi','branch_id':'m2'}])
        c.execute(legacy.tables['recipes'].insert(),[{'id':'known','tenant_id':'single','created_by':'u1','title':'Known','ingredients':'Arroz','instructions':'Cocinar'},{'id':'unknown','tenant_id':'multi','created_by':'u2','title':'Unknown','ingredients':'Arroz','instructions':'Cocinar'}])
        c.execute(legacy.tables['food_records'].insert(),{'id':'food','patient_id':'u2','result_json':'{}'})
    engine.dispose()
    upgrade(database)
    upgrade(database)
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    with engine.connect() as c:
        assert c.execute(text("SELECT branch_id FROM recipes WHERE id='known'")).scalar_one() == 's1'
        assert c.execute(text("SELECT branch_id FROM recipes WHERE id='unknown'")).scalar_one() is None
        assert tuple(c.execute(text("SELECT tenant_id,branch_id FROM food_records WHERE id='food'")).one()) == ('multi','m2')
    engine.dispose()


def upgrade(database):
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{database.as_posix()}', 'ENVIRONMENT': 'test'}
    result = subprocess.run([sys.executable, 'prepare_db.py'], cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_database_upgrade_and_seed_are_repeatable(tmp_path):
    database = tmp_path / 'fresh.sqlite'
    upgrade(database)
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM role_permissions WHERE role_id='CLIENTE'"))
    upgrade(database)
    assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM role_permissions WHERE role_id='CLIENTE'")).scalar_one() == 0
        assert connection.execute(text('SELECT COUNT(*) FROM users')).scalar_one() == 0
    engine.dispose()


def test_legacy_schema_adds_session_version_without_promoting_patient(tmp_path):
    database = tmp_path / 'legacy.sqlite'
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE users DROP COLUMN session_version'))
        connection.execute(text('ALTER TABLE tenants DROP COLUMN department_code'))
        connection.execute(text('ALTER TABLE branches DROP COLUMN province_code'))
        connection.execute(text('ALTER TABLE branches DROP COLUMN municipality_code'))
        connection.execute(text("INSERT INTO tenants(id,name,code,is_active,created_at,updated_at) VALUES ('legacy-clinic','Legacy clinic','LEGACY',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO branches(id,tenant_id,name,code,is_active,created_at) VALUES ('legacy-branch','legacy-clinic','Legacy branch','OLD',1,CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO roles(id,name,description,platform,created_at) VALUES ('CLIENTE','Paciente','Paciente','Mobile',CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO users(id,email,hashed_password,full_name,role_id,is_active,is_verified,created_at,updated_at) VALUES ('old','old@example.com','hash','Old patient','CLIENTE',1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    engine.dispose()
    upgrade(database)
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    with engine.connect() as connection:
        user = connection.execute(text("SELECT role_id,tenant_id,session_version FROM users WHERE id='old'")).one()
        assert tuple(user) == ('CLIENTE', None, 0)
        assert connection.execute(text("SELECT department_code FROM tenants WHERE id='legacy-clinic'")).scalar_one() is None
        assert tuple(connection.execute(text("SELECT province_code,municipality_code FROM branches WHERE id='legacy-branch'")).one()) == (None, None)
    engine.dispose()


import pytest

@pytest.mark.parametrize('scheme', ['postgres', 'postgresql', 'postgresql+psycopg', 'postgresql+psycopg2'])
def test_migrations_use_the_installed_postgresql_driver(monkeypatch, scheme):
    """Alembic and the API must both use psycopg2 without contacting a server."""
    import runpy
    from contextlib import nullcontext
    from alembic import context
    from alembic.config import Config
    from app.config import settings
    from app.database import _resolve_db_url
    from sqlalchemy.engine import make_url
    monkeypatch.setattr(settings, 'DATABASE_URL', scheme+'://test:p%40ss@db.invalid:5432/demo?sslmode=require')
    api_url = _resolve_db_url()
    recorded = {}
    monkeypatch.setattr(context, 'config', Config(), raising=False)
    monkeypatch.setattr(context, 'is_offline_mode', lambda: True)
    monkeypatch.setattr(context, 'configure', lambda **kw: recorded.update(kw))
    monkeypatch.setattr(context, 'begin_transaction', nullcontext)
    monkeypatch.setattr(context, 'run_migrations', lambda: None)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'alembic' / 'env.py'))
    assert recorded['url'] == api_url
    parsed = make_url(api_url)
    assert parsed.drivername == 'postgresql+psycopg2'
    assert parsed.password == 'p@ss'
    assert parsed.query['sslmode'] == 'require'
    engine = create_engine(api_url)
    assert engine.dialect.driver == 'psycopg2'
    engine.dispose()


def test_consultation_wallet_upgrades_existing_payment_table(tmp_path):
    database = tmp_path / 'before_wallet.sqlite'
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    legacy = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name != 'wallet_movements':
            table.to_metadata(legacy)
    payments = legacy.tables['payments']
    for field in ('patient_id', 'appointment_id', 'purpose'):
        column = payments.c[field]
        for index in list(payments.indexes):
            if field in index.columns: payments.indexes.remove(index)
        for constraint in list(payments.constraints):
            if field in constraint.columns: payments.constraints.remove(constraint)
        for fk in list(column.foreign_keys): payments.foreign_keys.discard(fk)
        payments._columns.remove(column)
    legacy.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('20261031_07')"))
        connection.execute(legacy.tables['tenants'].insert(),{'id':'old','name':'Empresa antigua','code':'OLD'})
        connection.execute(payments.insert(),{'id':'paid','tenant_id':'old','customer_name':'Cliente antiguo','concept':'Consulta','amount':25,'status':'COMPLETED','currency':'USD'})
    engine.dispose()
    upgrade(database)
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    assert 'wallet_movements' in inspect(engine).get_table_names()
    assert {'patient_id','appointment_id','purpose'} <= {c['name'] for c in inspect(engine).get_columns('payments')}
    with engine.connect() as connection:
        row = connection.execute(text("SELECT amount,purpose FROM payments WHERE id='paid'")).one()
        assert tuple(row) == (25,'POS')
    engine.dispose()
