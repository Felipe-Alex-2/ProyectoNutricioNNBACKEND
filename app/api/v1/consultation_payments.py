from typing import Literal
from fastapi import APIRouter, Depends, Header, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.database import get_db
from app.core.authorization import require_role
from app.config import settings
from app.services.consultation_payment_service import ConsultationPaymentService as Service

router = APIRouter(tags=['Consultation payments and wallet'])


@router.get('/my-payments')
def my_payments(db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.models.payment import Payment
    from app.services.payment_service import PaymentService
    require_role(user, {'CLIENTE'})
    rows = db.query(Payment).filter_by(patient_id=user.id).order_by(Payment.created_at.desc()).all()
    return [PaymentService._map_to_out(p) for p in rows]


@router.post('/my-payments/{payment_id}/checkout')
def resume_checkout(payment_id: str, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.models.payment import Payment
    require_role(user, {'CLIENTE'})
    payment = db.query(Payment).filter_by(id=payment_id, patient_id=user.id).with_for_update().first()
    if not payment:
        raise HTTPException(404, detail='Pago no encontrado.')
    if payment.status != 'PENDING' or payment.purpose not in {'CONSULTATION', 'WALLET_TOPUP'}:
        raise HTTPException(409, detail='El pago no está pendiente.')
    return Service.checkout(db, payment, mobile_return_url(request))


class Topup(BaseModel):
    amount: Literal[5, 10, 20, 50]


class Pay(BaseModel):
    method: Literal['WALLET', 'PAYPAL', 'EFECTIVO', 'ONLINE', 'PRESENCIAL']
    mobile: bool = False


def mobile_return_url(request):
    base = request.base_url
    if base.hostname not in {'localhost', '127.0.0.1', 'testserver'}:
        base = base.replace(scheme='https')
    return str(base).rstrip('/') + '/api/v1/payments/mobile-return'


@router.get('/payments/mobile-return', response_class=HTMLResponse)
def mobile_return():
    return '<!doctype html><html lang="es"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NutriSalud</title><body style="font-family:system-ui;padding:32px;background:#f2faf8;color:#075e55"><h1>Vuelve a NutriSalud</h1><p>Si aprobaste el pago en PayPal, vuelve a la app y pulsa «Verificar pago» para confirmar el resultado.</p><p>Si cancelaste, no se añadirá saldo ni se confirmará la cita.</p></body></html>'


@router.get('/wallet')
def wallet(db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_role(user, {'CLIENTE'})
    return Service.wallet(db, user)


@router.post('/wallet/topups')
def topup(body: Topup, request: Request, idempotency_key: str = Header(..., min_length=8, max_length=100),
          db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_role(user, {'CLIENTE'})
    return Service.topup(db, user, body.amount, mobile_return_url(request), idempotency_key)


@router.post('/appointments/{appointment_id}/pay')
def pay(appointment_id: str, body: Pay, request: Request,
        db: Session = Depends(get_db), user=Depends(get_current_user)):
    return_url = mobile_return_url(request) if body.mobile else settings.FRONTEND_URL.rstrip('/') + '/paypal-return'
    return Service.pay(db, user, appointment_id, body.method, return_url)
