"""Validated reporting plans executed with SQLAlchemy, never model-generated SQL."""
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from sqlalchemy import func, and_, or_, cast, String, case, not_
from sqlalchemy.orm import aliased
from app.models.user import User
from app.models.tenant import Tenant
from app.models.company import Branch
from app.models.payment import Payment
from app.models.appointment import Appointment
from app.models.recipe import Recipe
from app.models.clinical import ClinicalRecord
from app.models.activity_log import ActivityLog
from app.models.ai_nutrition import NutritionalPlan
from app.models.patient_link import PatientNutritionistLink
from app.core.authorization import require_role, STAFF_ROLES, require_tenant
from app.core.exceptions import ForbiddenException, BadRequestException
from app.schemas.report import ReportQueryResponse, ReportColumnMeta

SOURCES={'patients':User,'payments':Payment,'appointments':Appointment,'recipes':Recipe,'clinical_records':ClinicalRecord,'activity_logs':ActivityLog,'nutritional_plans':NutritionalPlan}
NUMBERS={'payments':{'amount'},'recipes':{'calories','protein','carbohydrates','fats','fiber','servings'},'nutritional_plans':{'daily_calories','protein_g','carbs_g','fats_g','meals_per_day'}}
LABELS={'patients':'clientes registrados','payments':'cobros','appointments':'citas','recipes':'recetas','clinical_records':'fichas clínicas','activity_logs':'operaciones','nutritional_plans':'planes nutricionales'}

class ReportAnalyticsService:
    @staticmethod
    def scope(db, actor, req):
        require_role(actor,STAFF_ROLES)
        requested=set(req.tenant_ids or ([req.tenant_id] if req.tenant_id else []))
        from app.core.workspace import current
        workspace = current(db)
        if workspace and workspace.tenant_id:
            if requested and requested != {workspace.tenant_id}: raise ForbiddenException('Cambia al espacio global para comparar empresas.')
            requested = {workspace.tenant_id}
            if workspace.branch_id and req.branch_ids and set(req.branch_ids) != {workspace.branch_id}:
                raise ForbiddenException('El reporte debe pertenecer a la sucursal activa.')
        if req.tenant_id and requested and req.tenant_id not in requested:
            raise BadRequestException('El filtro de empresa no coincide con las empresas solicitadas.')
        if actor.role_id!='SUPERADMIN':
            require_tenant(actor,actor.tenant_id)
            if requested and requested!={actor.tenant_id}:raise ForbiddenException('Solo puedes reportar datos de tu empresa.')
            requested={actor.tenant_id}
        companies=db.query(Tenant).filter(Tenant.id.in_(requested)).all() if requested else db.query(Tenant).all()
        if requested and {c.id for c in companies}!=requested:raise BadRequestException('Alguna empresa seleccionada no existe.')
        company_ids=[c.id for c in companies]
        branches=db.query(Branch).filter(Branch.tenant_id.in_(company_ids)).all()
        if workspace and workspace.branch_id: branches = [b for b in branches if b.id == workspace.branch_id]
        if req.branch_ids and not set(req.branch_ids)<={b.id for b in branches}:raise ForbiddenException('Alguna sucursal no pertenece al alcance autorizado.')
        return companies,branches

    @classmethod
    def source(cls,db,actor,req):
        companies,branches=cls.scope(db,actor,req)
        model=SOURCES.get(req.entity)
        if model is None:raise BadRequestException('Entidad de reporte no disponible.')
        q=db.query(model).filter(model.tenant_id.in_([c.id for c in companies]))
        if model is Payment:
            q=q.filter(Payment.purpose != 'WALLET_TOPUP')
        if model is User:
            q=q.filter(User.role_id=='CLIENTE');branch_col=User.branch_id
        else:branch_col=model.branch_id
        if req.branch_ids:q=q.filter(branch_col.in_(req.branch_ids))
        if model is ActivityLog:
            from app.services.audit_policy import MUTATION_WORDS, NON_MUTATION_WORDS
            q=q.filter(or_(*(ActivityLog.action.ilike('%'+word+'%') for word in MUTATION_WORDS)),not_(or_(*(ActivityLog.action.ilike('%'+word+'%') for word in NON_MUTATION_WORDS))))
        if actor.role_id=='NUTRICIONISTA':
            if model is User:
                linked=db.query(PatientNutritionistLink.patient_id).filter(PatientNutritionistLink.nutritionist_id==actor.id,PatientNutritionistLink.tenant_id==actor.tenant_id,PatientNutritionistLink.status=='LINKED')
                q=q.filter(User.id.in_(linked))
            elif model in (Appointment,ClinicalRecord,NutritionalPlan):q=q.filter(model.nutritionist_id==actor.id)
            elif model is ActivityLog:q=q.filter(model.user_id==actor.id)
        dt=model.scheduled_at if model is Appointment else model.paid_at if model is Payment and req.status=='COMPLETED' else model.created_at
        if model is Payment and req.status=='COMPLETED':dt=func.coalesce(Payment.paid_at,Payment.created_at)
        def boundary(value, advance=False):
            day=date.fromisoformat(value)+(timedelta(days=1) if advance else timedelta())
            local=datetime.combine(day,datetime.min.time())
            return local if model is ActivityLog else local+timedelta(hours=4)
        if req.start_date:q=q.filter(dt>=boundary(req.start_date))
        if req.end_date:q=q.filter(dt<boundary(req.end_date, True))
        if req.status:
            status=req.status.strip()
            if model is User:
                active=status.lower() in ('activo','active','true','1')
                if status.lower() not in ('activo','active','true','1','inactivo','inactive','false','0'):raise BadRequestException('Estado de paciente no válido.')
                q=q.filter(User.is_active.is_(active))
            elif model is Recipe:
                if status.lower() in ('activo','active'):q=q.filter(Recipe.is_active.is_(True))
                elif status.lower() in ('inactivo','inactive'):q=q.filter(Recipe.is_active.is_(False))
                else:q=q.filter(Recipe.category.ilike(status))
            elif model is ActivityLog:q=q.filter(ActivityLog.category==status.upper())
            elif hasattr(model,'status'):
                states={Appointment:{'PENDING','ACCEPTED','CONFIRMED','COMPLETED','CANCELLED'},Payment:{'PENDING','COMPLETED','CANCELLED','FAILED'},NutritionalPlan:{'DRAFT','APPROVED','REJECTED','ARCHIVED'}}
                if status.upper() not in states.get(model,set()):raise BadRequestException('Ese estado no está disponible para esta entidad.')
                q=q.filter(model.status==status.upper())
            else:raise BadRequestException('Esta entidad no tiene filtro por estado. Utiliza búsqueda o un campo específico.')
        if req.currency:
            if model is not Payment:raise BadRequestException('La moneda solo aplica a cobros.')
            q=q.filter(Payment.currency==req.currency)
        filter_fields={c.name:getattr(model,c.name) for c in model.__table__.columns if c.name in {'full_name','email','phone','is_active','created_at','status','scheduled_at','amount','currency','payment_method','customer_name','concept','title','category','calories','protein','carbohydrates','fats','fiber','servings','difficulty','diagnosis','clinical_goals','daily_calories','goal','action','user_name'}}
        for condition in req.filters:
            column=filter_fields.get(condition.field)
            if column is None:raise BadRequestException(f'El campo {condition.field} no está disponible para filtrar esta entidad.')
            value=condition.value;operator=condition.operator
            if operator!='in' and isinstance(value,(list,dict)) or isinstance(value,dict):raise BadRequestException('El filtro requiere un valor simple, o una lista con el operador in.')
            if condition.field in ('created_at','scheduled_at'):raise BadRequestException('Para fechas utiliza el periodo de inicio y fin del reporte.')
            if condition.field in NUMBERS.get(req.entity,set()) and operator!='in':
                if isinstance(value,bool) or isinstance(value,(dict,list)):raise BadRequestException('El filtro numérico requiere un número.')
                try:value=float(value)
                except (ValueError,TypeError):raise BadRequestException('El filtro numérico requiere un número.')
                import math
                if not math.isfinite(value):raise BadRequestException('El filtro numérico debe ser finito.')
            if operator=='in':
                if not isinstance(value,list) or len(value)>100:raise BadRequestException('El filtro de lista admite hasta 100 valores.')
                q=q.filter(column.in_(value))
            elif operator=='contains':q=q.filter(cast(column,String).ilike('%'+str(value)+'%'))
            elif operator=='eq':q=q.filter(column==value)
            elif operator=='ne':q=q.filter(column!=value)
            elif operator=='gt':q=q.filter(column>value)
            elif operator=='gte':q=q.filter(column>=value)
            elif operator=='lt':q=q.filter(column<value)
            elif operator=='lte':q=q.filter(column<=value)
        if req.search:
            term='%'+req.search.strip()+'%'
            fields=[getattr(model,k) for k in ('full_name','email','phone','title','concept','customer_name','customer_email','diagnosis','clinical_goals','evolution_notes','description','action','user_name','user_email','reason','goal','category','difficulty','clinical_notes') if hasattr(model,k)]
            matches=[f.ilike(term) for f in fields]
            if model is not User:
                people=db.query(User.id).filter(or_(User.full_name.ilike(term),User.email.ilike(term),User.phone.ilike(term)))
                for key in ('patient_id','nutritionist_id'):
                    if hasattr(model,key): matches.append(getattr(model,key).in_(people))
            matches.append(model.tenant_id.in_(db.query(Tenant.id).filter(Tenant.name.ilike(term))))
            matches.append(branch_col.in_(db.query(Branch.id).filter(Branch.name.ilike(term))))
            q=q.filter(or_(*matches))
        return q,model,branch_col,dt,companies,branches

    @classmethod
    def query(cls,db,req,actor):
        q,model,branch_col,dt,companies,branches=cls.source(db,actor,req)
        groups=[]; columns=[]
        if req.group_by in ('company','branch'):
            q=q.outerjoin(Tenant,Tenant.id==model.tenant_id)
            groups=[model.tenant_id.label('company_id'),Tenant.name.label('company_name')]
            columns=[ReportColumnMeta(key='company_name',label='Empresa')]
            if req.group_by=='branch':
                q=q.outerjoin(Branch,and_(Branch.id==branch_col,Branch.tenant_id==model.tenant_id))
                groups.extend([Branch.id.label('branch_id'),func.coalesce(Branch.name,'Sin sucursal asignada').label('branch_name')])
                columns.append(ReportColumnMeta(key='branch_name',label='Sucursal'))
        elif req.group_by=='month':
            local_dt=dt if model is ActivityLog else func.datetime(dt,'-4 hours') if db.bind.dialect.name=='sqlite' else func.timezone('America/La_Paz',dt if model is Payment else func.timezone('UTC',dt))
            month=func.strftime('%Y-%m',local_dt) if db.bind.dialect.name=='sqlite' else func.to_char(local_dt,'YYYY-MM')
            groups=[month.label('month')];columns=[ReportColumnMeta(key='month',label='Mes')]
        elif req.group_by=='nutritionist':
            if not hasattr(model,'nutritionist_id'):raise BadRequestException('Agrupa por especialista las citas, fichas o planes nutricionales.')
            doctor=aliased(User);q=q.outerjoin(doctor,doctor.id==model.nutritionist_id)
            groups=[model.nutritionist_id.label('nutritionist_id'),func.coalesce(doctor.full_name,'Sin especialista').label('nutritionist_name')];columns=[ReportColumnMeta(key='nutritionist_name',label='Nutricionista')]
        elif req.group_by in ('status','payment_method'):
            if not hasattr(model,req.group_by):raise BadRequestException('Esta agrupación no aplica a la entidad seleccionada.')
            groups=[getattr(model,req.group_by).label(req.group_by)];columns=[ReportColumnMeta(key=req.group_by,label='Estado' if req.group_by=='status' else 'Método de pago')]
        metric_label='Cantidad'
        if req.metric=='count':measure=func.count(model.id)
        elif req.metric=='cancellation_rate':
            if model is not Appointment or req.status:raise BadRequestException('La tasa de cancelación requiere citas de todos los estados en el periodo.')
            measure=100.0*func.sum(case((Appointment.status=='CANCELLED',1),else_=0))/func.nullif(func.count(Appointment.id),0);metric_label='Cancelaciones (%)'
        elif req.metric=='unique_patients':
            if model is User:patient=model.id
            elif hasattr(model,'patient_id'):patient=model.patient_id
            else:raise BadRequestException('No se pueden identificar pacientes únicos en este conjunto de datos.')
            measure=func.count(func.distinct(patient));metric_label='Pacientes únicos'
        else:
            field=req.value_field or ('amount' if model is Payment else None)
            if field not in NUMBERS.get(req.entity,set()):raise BadRequestException('Selecciona un campo numérico válido para sumar o promediar.')
            measure=(func.sum if req.metric=='sum' else func.avg)(getattr(model,field));metric_label=('Total ' if req.metric=='sum' else 'Promedio ')+(('cobrado' if req.status=='COMPLETED' else 'registrado') if field=='amount' else field)
        if model is Payment and req.metric in ('sum','average'):
            groups.append(Payment.currency.label('currency'));columns.append(ReportColumnMeta(key='currency',label='Moneda'))
        data=q.with_entities(*groups,measure.label('value')).group_by(*groups).all() if groups else q.with_entities(measure.label('value')).all()
        rows=[dict(row._mapping) for row in data]
        for row in rows:
            value=row['value'] or 0;row['value']=round(float(value),2) if req.metric in ('sum','average','cancellation_rate') else int(value)
        # Include empty companies/branches in count rankings. The aggregation is computed before limit.
        if req.metric in ('count','unique_patients') and req.group_by in ('company','branch') and not req.search:
            if req.group_by=='company':
                present={r['company_id'] for r in rows}
                rows.extend({'company_id':c.id,'company_name':c.name,'value':0} for c in companies if c.id not in present)
            else:
                present={r['branch_id'] for r in rows};names={c.id:c.name for c in companies}
                rows.extend({'company_id':b.tenant_id,'company_name':names[b.tenant_id],'branch_id':b.id,'branch_name':b.name,'value':0} for b in branches if b.id not in present and (not req.branch_ids or b.id in req.branch_ids))
        key=req.sort_by or ('month' if req.group_by=='month' else 'value')
        allowed={g.name for g in groups}|{'value'}
        if key not in allowed:raise BadRequestException('La columna de orden no corresponde al reporte agrupado.')
        rows.sort(key=lambda r:str(r.get('company_name',''))+' '+str(r.get('branch_name','')))
        rows.sort(key=lambda r:r.get(key) if r.get(key) is not None else '',reverse=req.sort_order=='desc')
        total=len(rows);rows=rows[:req.limit or 500]
        columns.append(ReportColumnMeta(key='value',label=metric_label,type='number'))
        title=f"{metric_label.capitalize()} de {LABELS[req.entity]}"+({'company':' por empresa','branch':' por sucursal','month':' por mes','nutritionist':' por especialista','status':' por estado','payment_method':' por método de pago'}.get(req.group_by,''))
        notes=['Clientes = cuentas con rol paciente; no incluye administradores ni especialistas.'] if model is User else []
        if req.start_date or req.end_date:notes.append(f"Periodo: {req.start_date or 'inicio'} a {req.end_date or 'actualidad'}; aplicado a "+('fecha de registro.' if model is User else 'fecha de la cita.' if model is Appointment else 'fecha de cobro.' if model is Payment and req.status=='COMPLETED' else 'fecha de creación.'))
        if req.start_date or req.end_date or req.group_by=='month':notes.append('Fechas y agrupación mensual en horario de Bolivia (UTC−4).')
        if req.status:notes.append('Estado: '+req.status+'.')
        if req.metric=='cancellation_rate':notes.append('Tasa de cancelación = citas canceladas / todas las citas del periodo × 100.')
        for f in req.filters:notes.append(f'Filtro: {f.field} {f.operator} {f.value}.')
        if model is Payment and not req.status:notes.append('Se incluyen cobros de todos los estados. Para ingresos efectivamente cobrados selecciona COMPLETED.')
        if model is Payment and req.metric in ('sum','average'):notes.append('Los importes se separan por moneda; no se suman monedas distintas.')
        if req.group_by=='branch':notes.append('La sucursal corresponde a la atribución guardada de cada registro.')
        if any(r.get('branch_name')=='Sin sucursal asignada' for r in rows):notes.append('Los registros sin sucursal se muestran por separado; no se atribuyen a una sucursal arbitraria.')
        if total>len(rows):notes.append(f'Mostrando {len(rows)} de {total} grupos; los cálculos incluyen todos los registros autorizados.')
        summary=f'{total} grupos encontrados.' if groups else f'{metric_label}: {rows[0]["value"] if rows else 0}.'
        multiple_currencies = len({r.get('currency') for r in rows if r.get('currency')}) > 1
        if multiple_currencies:summary='Hay varias monedas. Los importes se muestran separados; selecciona una moneda para comparar importes entre empresas o sucursales.'
        if req.group_by in ('company','branch') and rows and not multiple_currencies:
            leading=rows[0];name=leading.get('branch_name') or leading.get('company_name');peers=[r for r in rows if r['value']==leading['value'] and r.get('currency')==leading.get('currency')]
            if not leading['value'] and not any(r['value'] for r in rows):summary='No hay registros que cumplan los filtros; no se puede identificar un líder.'
            elif req.sort_order=='desc':summary=f"{name} encabeza el reporte con {leading['value']}"+(' '+leading['currency'] if leading.get('currency') else '')+('. Hay empate en el valor mostrado.' if len(peers)>1 else '.')
            else:summary=f"{name} presenta el menor valor: {leading['value']}."
        return ReportQueryResponse(entity=req.entity,title=title,generated_at=datetime.now(timezone.utc),columns=columns,rows=rows,total_rows=total,summary=summary,notes=notes,chart='line' if req.group_by=='month' else 'bar' if req.group_by else None)
