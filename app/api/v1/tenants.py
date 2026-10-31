from typing import List, Optional
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.tenant import Tenant
from app.core.authorization import require_role, require_tenant
from app.schemas.user import UserCreate
from app.schemas.tenant import TenantCreate, TenantUpdate, TenantResponse
from app.services.tenant_service import TenantService

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.get("", response_model=List[TenantResponse])
def list_tenants(
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role_id != "SUPERADMIN":
        return [TenantService.get_by_id(db, current_user.tenant_id)] if current_user.tenant_id else []
    return TenantService.get_all(db, active_only=active_only)


@router.get("/public")
def list_public_tenants(
    db: Session = Depends(get_db),
):
    """Retorna las sucursales/clínicas activas disponibles públicamente para el registro de clientes."""
    return [{"id":t.id,"name":t.name,"code":t.code,"department_code":t.department_code} for t in db.query(Tenant).filter_by(is_active=True).all()]


@router.get('/administrator-candidates')
def administrator_candidates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_role(current_user, {'SUPERADMIN'})
    return [{'id':u.id,'full_name':u.full_name,'email':u.email} for u in db.query(User).filter(User.role_id == 'ADMIN_ORGANIZATION', User.tenant_id.is_(None), User.is_active.is_(True)).all()]


@router.post('/administrator-candidates', status_code=201)
def create_administrator_candidate(data: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.services.user_service import UserService
    from app.core.exceptions import ConflictException
    require_role(current_user, {'SUPERADMIN'})
    if UserService.get_by_email(db, str(data.email).lower()): raise ConflictException('El correo ya está registrado.')
    data.tenant_id = None
    account = UserService.create(db, data, role_id='ADMIN_ORGANIZATION')
    return {'id':account.id, 'full_name':account.full_name, 'email':account.email}


@router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_tenant(current_user, tenant_id)
    return TenantService.get_by_id(db, tenant_id)


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    data: TenantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, {"SUPERADMIN"})
    from sqlalchemy.exc import IntegrityError, OperationalError
    from app.core.exceptions import ConflictException
    from fastapi import HTTPException
    try:
        return TenantService.create(db, data)
    except IntegrityError:
        db.rollback()
        raise ConflictException('No se pudo crear: el nombre, código o administrador ya está registrado. Actualiza la lista.')
    except OperationalError:
        db.rollback()
        raise HTTPException(status_code=503, detail='La base de datos no respondió a tiempo. Actualiza la lista para comprobar si la empresa se creó antes de reintentar.')


@router.put("/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: str,
    data: TenantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, {"SUPERADMIN"})
    return TenantService.update(db, tenant_id, data)


@router.patch("/{tenant_id}/toggle", response_model=TenantResponse)
def toggle_tenant_status(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, {"SUPERADMIN"})
    return TenantService.toggle_status(db, tenant_id)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, {"SUPERADMIN"})
    TenantService.delete(db, tenant_id)
    return None
