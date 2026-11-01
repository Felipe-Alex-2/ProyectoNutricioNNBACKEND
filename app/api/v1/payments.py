"""
Point-of-Sale Payments Router (Caja y Cobros con PayPal Sandbox).
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, get_current_user_optional
from app.models.user import User
from app.models.payment import Payment
from app.core.authorization import require_role, require_tenant, STAFF_ROLES
from app.config import settings
from app.schemas.payment import (
    PaymentCaptureRequest,
    PaymentCreate,
    PaymentOrderCreatedOut,
    PaymentOut,
    PaymentStatsOut,
)
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments POS"])


@router.post("/create", response_model=PaymentOrderCreatedOut)
def create_pos_payment(
    body: PaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, STAFF_ROLES)
    require_tenant(current_user, body.tenant_id)
    """
    Crea una orden de cobro en caja e inicia el checkout de PayPal Sandbox.
    Retorna la URL de PayPal (approval_url) para realizar el pago.
    """
    # Detect frontend origin if available
    frontend_url = settings.FRONTEND_URL.rstrip("/")

    return PaymentService.create_payment(
        db=db,
        cashier=current_user,
        data=body,
        frontend_url=frontend_url,
    )


@router.post("/capture", response_model=PaymentOut)
def capture_pos_payment(
    body: PaymentCaptureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize_payment(db, current_user, order_id=body.paypal_order_id)
    """
    Captura y confirma el cobro tras la aprobación del cliente en PayPal Sandbox.
    """
    return PaymentService.capture_payment(
        db=db,
        paypal_order_id=body.paypal_order_id,
    )


@router.get("", response_model=List[PaymentOut])
def list_payments(
    tenant_id: Optional[str] = Query(None, description="Filtrar por ID de sucursal"),
    status: Optional[str] = Query(None, description="Filtrar por estado (PENDING, COMPLETED, CANCELLED)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, STAFF_ROLES)
    if current_user.role_id != "SUPERADMIN":
        require_tenant(current_user, current_user.tenant_id)
    """
    Lista el historial de cobros realizados en la sucursal seleccionada o general.
    """
    # Si el usuario es ADMIN_ORGANIZATION o NUTRICIONISTA y tiene tenant_id, filtrar por defecto si no es SAAS
    effective_tenant_id = tenant_id
    if current_user.role_id != "SUPERADMIN" and current_user.tenant_id:
        effective_tenant_id = current_user.tenant_id

    return PaymentService.list_payments(
        db=db,
        tenant_id=effective_tenant_id,
        status_filter=status,
    )


@router.get("/stats", response_model=PaymentStatsOut)
def get_payment_stats(
    tenant_id: Optional[str] = Query(None, description="Filtrar estadísticas por ID de sucursal"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_role(current_user, STAFF_ROLES)
    if current_user.role_id != "SUPERADMIN":
        require_tenant(current_user, current_user.tenant_id)
    """
    Retorna métricas de caja (total recaudado, cobros completados y pendientes).
    """
    effective_tenant_id = tenant_id
    if current_user.role_id != "SUPERADMIN" and current_user.tenant_id:
        effective_tenant_id = current_user.tenant_id

    return PaymentService.get_payment_stats(
        db=db,
        tenant_id=effective_tenant_id,
    )


@router.post("/{payment_id}/cancel", response_model=PaymentOut)
def cancel_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payment = _authorize_payment(db, current_user, payment_id=payment_id)
    require_role(current_user, STAFF_ROLES)
    if payment.purpose != 'POS':
        raise HTTPException(409, detail='Los cobros de citas y recargas se gestionan desde su operación de origen.')
    """
    Cancela un cobro pendiente en caja.
    """
    return PaymentService.cancel_payment(
        db=db,
        payment_id=payment_id,
    )


@router.get("/{payment_id}/pdf")
def export_payment_pdf(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorize_payment(db, current_user, payment_id=payment_id)
    """
    Genera y descarga el archivo PDF oficial con el Detalle de Pago (comprobante / recibo).
    """
    pdf_stream, filename = PaymentService.generate_payment_pdf(db=db, payment_id=payment_id)
    return StreamingResponse(
        pdf_stream,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _authorize_payment(db, actor, payment_id=None, order_id=None):
    query = db.query(Payment)
    payment = query.filter(Payment.id == payment_id).first() if payment_id else query.filter(Payment.paypal_order_id == order_id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="Cobro no encontrado.")
    if actor.role_id == 'CLIENTE':
        if payment.patient_id != actor.id:
            raise HTTPException(403, detail='Solo puedes consultar tus pagos.')
    else:
        require_role(actor, STAFF_ROLES)
        require_tenant(actor, payment.tenant_id)
    return payment
