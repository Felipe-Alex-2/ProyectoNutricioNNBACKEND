"""Only business mutations are eligible for the activity log."""
from uuid import uuid4
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session


NON_MUTATION_WORDS = ('NAVEG', 'LOGIN', 'LOGOUT', 'ACCESO', 'VISUAL', 'VERIFIC', 'CAMBIO_TEMA', 'DESBLOQUE')
MUTATION_WORDS = ('CREA', 'REGISTR', 'ACTUALIZ', 'UPDATE', 'EDIT', 'MODIFIC', 'ELIMIN', 'DELETE', 'BORRA', 'DESACTIV', 'ACTIVAD', 'CANCEL', 'CONFIRM', 'ASIGNA', 'VINCUL', 'APROB', 'GUARD', 'RESTAUR', 'BACKUP', 'EXPORT', 'PAGO', 'COBRO', 'ANAMNESIS', 'DIAGNOST', 'SUSCRIP', 'ATENDIDA')


def is_mutation(action):
    value = (action or '').upper()
    return not any(word in value for word in NON_MUTATION_WORDS) and any(word in value for word in MUTATION_WORDS)


def _valid_fk(db: Session, table_cls, candidate_id: str | None, new_ids: set[str | None], deleted_ids: set[str | None]) -> str | None:
    if not candidate_id or candidate_id in deleted_ids:
        return None
    if candidate_id in new_ids:
        return candidate_id
    with db.no_autoflush:
        exists = db.execute(select(table_cls.__table__.c.id).where(table_cls.__table__.c.id == candidate_id)).scalar_one_or_none()
        return candidate_id if exists else None


@event.listens_for(Session, 'before_flush')
def audit_business_changes(db, flush_context, instances):
    from app.models.activity_log import ActivityLog
    from app.models.user import User
    from app.models.tenant import Tenant
    from app.models.company import Branch

    for obj in list(db.new):
        if getattr(obj, '__tablename__', '') in {'tenants', 'branches'} and hasattr(obj, 'id') and obj.id is None:
            obj.id = str(uuid4())

    new_tenant_ids = {getattr(o, 'id', None) for o in db.new if getattr(o, '__tablename__', '') == 'tenants'}
    new_branch_ids = {getattr(o, 'id', None) for o in db.new if getattr(o, '__tablename__', '') == 'branches'}
    deleted_tenant_ids = {getattr(o, 'id', None) for o in db.deleted if getattr(o, '__tablename__', '') == 'tenants'}
    deleted_branch_ids = {getattr(o, 'id', None) for o in db.deleted if getattr(o, '__tablename__', '') == 'branches'}

    for log in list(db.new):
        if isinstance(log, ActivityLog):
            log.ip_address = None
            if not is_mutation(log.action):
                db.expunge(log)
                continue
            if not log.tenant_id and log.user_id:
                with db.no_autoflush:
                    user = db.get(User, log.user_id)
                    log.tenant_id = user.tenant_id if user else None
            log.tenant_id = _valid_fk(db, Tenant, log.tenant_id, new_tenant_ids, deleted_tenant_ids)
            log.branch_id = _valid_fk(db, Branch, log.branch_id, new_branch_ids, deleted_branch_ids)

    actor = db.info.get('audit_actor')
    if actor is None:
        return
    excluded = {'activity_logs', 'token_blacklist', 'backup_logs', 'backup_settings', 'device_tokens'}
    for state, objects in [('CREADO', list(db.new)), ('ACTUALIZADO', list(db.dirty)), ('ELIMINADO', list(db.deleted))]:
        for obj in objects:
            table = getattr(obj, '__tablename__', '')
            if not table or table in excluded:
                continue
            if state == 'ACTUALIZADO':
                changed = {attr.key for attr in inspect(obj).attrs if attr.history.has_changes()}
                if not changed or changed <= {'session_version', 'updated_at', 'is_read', 'reminder_sent', 'workspace_tenant_id', 'workspace_branch_id'}:
                    continue
            if hasattr(obj, 'id') and obj.id is None:
                obj.id = str(uuid4())
            company = getattr(obj, 'tenant_id', None) or (obj.id if table == 'tenants' and state != 'ELIMINADO' else actor.tenant_id)
            label = getattr(obj, 'title', None) or getattr(obj, 'name', None) or getattr(obj, 'full_name', None) or getattr(obj, 'id', '')
            action = f'{table.upper()}_{state}'
            if table == 'appointments' and state == 'ACTUALIZADO':
                action = {'CONFIRMED': 'CITA_CONFIRMADA', 'CANCELLED': 'CITA_CANCELADA', 'COMPLETED': 'CITA_ATENDIDA'}.get(obj.status, action)
            if table == 'payments' and getattr(obj, 'status', '') == 'COMPLETED' and (state == 'CREADO' or inspect(obj).attrs.status.history.has_changes()):
                action = 'COBRO_CONFIRMADO'
            from app.core.workspace import current
            scope = current(db)
            raw_tenant_id = company or (scope.tenant_id if scope else None)
            raw_branch_id = scope.branch_id if scope and scope.branch_id else (obj.id if table == 'branches' and state != 'ELIMINADO' else getattr(obj, 'branch_id', None))
            resolved_tenant_id = _valid_fk(db, Tenant, raw_tenant_id, new_tenant_ids, deleted_tenant_ids)
            resolved_branch_id = _valid_fk(db, Branch, raw_branch_id, new_branch_ids, deleted_branch_ids)
            db.add(ActivityLog(
                tenant_id=resolved_tenant_id,
                branch_id=resolved_branch_id,
                user_id=actor.id,
                user_email=actor.email,
                user_name=actor.full_name,
                action=action,
                description=f'{action}: {table} · {str(label)[:200]}',
                category='OPERACIONES',
            ))
