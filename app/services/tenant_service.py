from typing import List, Optional
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from app.models.tenant import Tenant
from app.models.company import Branch, CompanyAdministrator
from app.core.exceptions import BadRequestException
from app.models.user import User
from app.schemas.tenant import TenantCreate, TenantUpdate
from app.services.bolivia_locations import validate_department, validate_location
from app.core.exceptions import ConflictException, NotFoundException


class TenantService:
    @staticmethod
    def get_all(db: Session, active_only: bool = False) -> List[Tenant]:
        query = db.query(Tenant)
        if active_only:
            query = query.filter(Tenant.is_active.is_(True))
        tenants = query.order_by(Tenant.created_at.desc()).all()

        # Augment with users count
        for t in tenants:
            assignment = db.get(CompanyAdministrator, t.id)
            t.administrator_id = assignment.user_id if assignment else None
            t.users_count = db.query(User).filter(User.tenant_id == t.id).count()
        return tenants

    @staticmethod
    def get_by_id(db: Session, tenant_id: str) -> Tenant:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise NotFoundException("Organización / Tenant no encontrado")
        assignment = db.get(CompanyAdministrator, tenant.id)
        tenant.administrator_id = assignment.user_id if assignment else None
        tenant.users_count = db.query(User).filter(User.tenant_id == tenant.id).count()
        return tenant

    @staticmethod
    def create(db: Session, data: TenantCreate) -> Tenant:
        if db.get_bind().dialect.name == 'postgresql':
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.execute(text("SET LOCAL statement_timeout = '20s'"))
            if not db.execute(text('SELECT pg_try_advisory_xact_lock(728451)')).scalar():
                raise ConflictException('Hay otra creación de empresa en proceso. Actualiza la lista y vuelve a intentarlo.')
        if not data.administrator_id: raise BadRequestException('Selecciona una cuenta administradora para la empresa.')
        administrator = db.get(User, data.administrator_id)
        if not administrator or not administrator.is_active or administrator.role_id != 'ADMIN_ORGANIZATION':
            raise BadRequestException('Selecciona una cuenta activa con rol Administrador.')
        if administrator.tenant_id or db.query(CompanyAdministrator).filter_by(user_id=administrator.id).first():
            raise BadRequestException('La cuenta administradora ya pertenece a una empresa.')
        validate_location(data.department_code, data.initial_province_code, data.initial_municipality_code)
        # Validacion 1: Campos vacios
        clean_name = data.name.strip() if data.name else ""
        clean_code = data.code.strip().upper() if data.code else ""
        if not clean_name or not clean_code:
            raise ConflictException("No se puede crear la sucursal con campos vacios. Debe ingresar el nombre y el codigo identificador.")

        # Validacion 2: Nombre unico
        existing_name = next((row for row in db.query(Tenant).all() if ' '.join(row.name.split()).casefold() == clean_name.casefold()), None)
        if existing_name:
            raise ConflictException(f"Ya existe una sucursal con el nombre '{clean_name}'. No se permite duplicar nombres de sucursales.")

        # Validacion 3: Codigo unico
        existing = db.query(Tenant).filter(func.upper(func.trim(Tenant.code)) == clean_code).first()
        if existing:
            raise ConflictException(f"Ya existe una sucursal con el codigo '{clean_code}'. No se permite duplicar codigos de sucursales.")

        tenant = Tenant(
            name=clean_name,
            code=clean_code,
            phone=data.phone.strip() if data.phone else None,
            email=data.email.strip() if data.email else None,
            address=data.address.strip() if data.address else None,
            logo_url=data.logo_url.strip() if data.logo_url else None,
            description=data.description.strip() if data.description else None,
            is_active=data.is_active,
            department_code=data.department_code,
        )
        db.add(tenant)
        db.flush()
        raw_branch_code = data.initial_branch_code.strip().upper()
        branch_code = f'{clean_code}-PRINCIPAL'[:50] if raw_branch_code == 'PRINCIPAL' and db.query(Branch).filter(func.upper(func.trim(Branch.code)) == 'PRINCIPAL').first() else raw_branch_code
        branch = Branch(tenant_id=tenant.id, name=data.initial_branch_name.strip(), code=branch_code, address=data.address, province_code=data.initial_province_code, municipality_code=data.initial_municipality_code)
        db.add(branch); db.flush()
        administrator.tenant_id = tenant.id; administrator.branch_id = branch.id
        administrator.session_version = (administrator.session_version or 0) + 1
        db.add(CompanyAdministrator(tenant_id=tenant.id, user_id=administrator.id))
        db.commit()
        db.refresh(tenant)
        tenant.administrator_id = administrator.id
        tenant.users_count = 1
        return tenant

    @staticmethod
    def update(db: Session, tenant_id: str, data: TenantUpdate) -> Tenant:
        tenant = TenantService.get_by_id(db, tenant_id)
        update_data = data.model_dump(exclude_unset=True)
        if 'department_code' in update_data:
            code = update_data['department_code']
            validate_department(code)
            if code != tenant.department_code:
                for branch in db.query(Branch).filter_by(tenant_id=tenant.id).all():
                    if branch.province_code or branch.municipality_code:
                        validate_location(code, branch.province_code, branch.municipality_code)

        if "name" in update_data and update_data["name"]:
            clean_name = update_data["name"].strip()
            if not clean_name:
                raise ConflictException("El nombre de la sucursal no puede estar vacio.")
            existing_name = db.query(Tenant).filter(
                func.lower(Tenant.name) == clean_name.lower(),
                Tenant.id != tenant_id,
            ).first()
            if existing_name:
                raise ConflictException(f"Ya existe otra sucursal con el nombre '{clean_name}'. No se permite duplicar nombres de sucursales.")
            update_data["name"] = clean_name

        for key, value in update_data.items():
            setattr(tenant, key, value)
        db.commit()
        db.refresh(tenant)
        tenant.users_count = db.query(User).filter(User.tenant_id == tenant.id).count()
        return tenant

    @staticmethod
    def toggle_status(db: Session, tenant_id: str) -> Tenant:
        tenant = TenantService.get_by_id(db, tenant_id)
        tenant.is_active = not tenant.is_active  # type: ignore[assignment]
        db.commit()
        db.refresh(tenant)
        tenant.users_count = db.query(User).filter(User.tenant_id == tenant.id).count()
        return tenant

    @staticmethod
    def delete(db: Session, tenant_id: str) -> None:
        tenant = TenantService.get_by_id(db, tenant_id)
        if db.query(User).filter(User.tenant_id == tenant.id).first():
            raise ConflictException('La empresa tiene cuentas u operaciones asociadas. Desactívala para conservar sus datos.')
        from app.database import Base
        from sqlalchemy import select
        for table in Base.metadata.sorted_tables:
            if 'tenant_id' in table.c and table.name not in {'branches', 'company_administrators'}:
                if db.execute(select(table.c.tenant_id).where(table.c.tenant_id == tenant.id).limit(1)).first():
                    raise ConflictException('La empresa contiene operaciones. Desactívala para conservar sus datos.')
        db.delete(tenant)
        db.commit()
