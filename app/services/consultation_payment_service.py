"""Accepted appointments cost USD 5. Wallet entries are immutable and atomic."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func
from app.models.appointment import Appointment
from app.models.payment import Payment
from app.models.wallet import WalletMovement
from app.models.user import User
from app.services.paypal_service import PayPalService


class ConsultationPaymentService:
    @staticmethod
    def ensure_charge(db, appointment):
        charge = db.query(Payment).filter_by(appointment_id=appointment.id).first()
        if charge:
            return charge
        patient = db.get(User, appointment.patient_id)
        charge = Payment(id=str(uuid4()), tenant_id=appointment.tenant_id,
                         branch_id=appointment.branch_id, patient_id=patient.id,
                         appointment_id=appointment.id, purpose='CONSULTATION',
                         customer_name=patient.full_name, customer_email=patient.email,
                         concept='Consulta general', amount=5, currency='USD',
                         status='PENDING', payment_method='PENDING')
        db.add(charge)
        db.flush()
        return charge

    @staticmethod
    def balance(db, user_id):
        return Decimal(str(db.query(func.coalesce(func.sum(WalletMovement.amount), 0))
                           .filter_by(user_id=user_id).scalar()))

    @staticmethod
    def wallet(db, user):
        entries = db.query(WalletMovement).filter_by(user_id=user.id).order_by(WalletMovement.created_at.desc()).all()
        pending = db.query(Payment).filter_by(patient_id=user.id, purpose='WALLET_TOPUP', status='PENDING').all()
        return {'balance': float(ConsultationPaymentService.balance(db, user.id)), 'currency': 'USD',
                'movements': [{'id': e.id, 'amount': float(e.amount), 'concept': e.concept,
                               'created_at': e.created_at, 'payment_id': e.payment_id} for e in entries],
                'pending_topups': [{'payment_id': p.id, 'amount': p.amount, 'paypal_order_id': p.paypal_order_id} for p in pending]}

    @staticmethod
    def checkout(db, payment, return_url):
        # Reuse the persisted order; retries must never create two payable orders.
        try:
            if payment.paypal_order_id:
                order = PayPalService.get_order(payment.paypal_order_id)
                approval = next((l['href'] for l in order.get('links', []) if l.get('rel') in {'approve', 'payer-action'}), None)
                return {'payment_id': payment.id, 'paypal_order_id': payment.paypal_order_id, 'approval_url': approval}
            order = PayPalService.create_order(amount=payment.amount, currency='USD',
                description=payment.concept, return_url=return_url, cancel_url=return_url,
                request_id=payment.id)
        except Exception:
            raise HTTPException(502, detail='No se pudo abrir PayPal. Intenta nuevamente; no se ha acreditado ningún pago.')
        payment.paypal_order_id = order['order_id']
        payment.payment_method = 'PAYPAL'
        db.commit()
        return {'payment_id': payment.id, 'paypal_order_id': payment.paypal_order_id, 'approval_url': order['approval_url']}

    @staticmethod
    def topup(db, user, amount, return_url, key):
        from uuid import uuid5, NAMESPACE_URL
        if amount not in {5, 10, 20, 50}:
            raise HTTPException(422, detail='Selecciona USD 5, 10, 20 o 50.')
        if not user.tenant_id or not user.branch_id:
            raise HTTPException(400, detail='Tu cuenta necesita una empresa y sucursal asignadas.')
        db.query(User).filter_by(id=user.id).with_for_update().one()
        payment_id = str(uuid5(NAMESPACE_URL, f'wallet-topup:{user.id}:{key}'))
        payment = db.get(Payment, payment_id)
        if payment and payment.amount != amount:
            raise HTTPException(409, detail='Este reintento corresponde a otra recarga.')
        if payment and payment.status == 'COMPLETED':
            return {'payment_id': payment.id, 'status': 'COMPLETED'}
        if not payment:
            payment = Payment(id=payment_id, tenant_id=user.tenant_id, branch_id=user.branch_id,
                patient_id=user.id, purpose='WALLET_TOPUP', customer_name=user.full_name,
                customer_email=user.email, concept=f'Recarga wallet USD {amount}', amount=amount,
                currency='USD', status='PENDING', payment_method='PAYPAL')
            db.add(payment)
            # Persist the idempotency key before contacting the external provider.
            db.commit()
        payment = db.query(Payment).filter_by(id=payment_id).with_for_update().one()
        return ConsultationPaymentService.checkout(db, payment, return_url)

    @staticmethod
    def pay(db, user, appointment_id, method, return_url):
        from app.core.authorization import require_role, require_tenant, STAFF_ROLES
        appointment = db.query(Appointment).filter_by(id=appointment_id).with_for_update().first()
        if not appointment:
            raise HTTPException(404, detail='Cita no encontrada.')
        if user.role_id == 'CLIENTE':
            if appointment.patient_id != user.id:
                raise HTTPException(403, detail='Solo puedes pagar tus citas.')
            if method not in {'WALLET', 'PAYPAL', 'ONLINE', 'PRESENCIAL'}:
                raise HTTPException(403, detail='Selecciona pago online o presencial.')
        else:
            require_role(user, STAFF_ROLES)
            require_tenant(user, appointment.tenant_id)
            if method not in {'EFECTIVO', 'PAYPAL'}:
                raise HTTPException(422, detail='Selecciona efectivo o PayPal.')
        payment = db.query(Payment).filter_by(appointment_id=appointment.id).with_for_update().first()
        if payment and payment.status == 'COMPLETED':
            return {'payment_id': payment.id, 'status': 'COMPLETED'}
        if appointment.status != 'ACCEPTED':
            raise HTTPException(409, detail='La cita debe ser aceptada antes de pagar.')
        payment = payment or ConsultationPaymentService.ensure_charge(db, appointment)
        if method != 'PAYPAL' and payment.paypal_order_id:
            raise HTTPException(409, detail='Ya hay una orden PayPal. Verifica ese pago para evitar un cobro doble.')
        if method in {'ONLINE', 'PRESENCIAL'}:
            payment.payment_method = method
            db.commit()
            return {'payment_id': payment.id, 'status': 'PENDING', 'payment_preference': method}
        if method == 'PAYPAL':
            return ConsultationPaymentService.checkout(db, payment, return_url)
        if method == 'WALLET':
            db.query(User).filter_by(id=user.id).with_for_update().one()
            if ConsultationPaymentService.balance(db, user.id) < Decimal('5'):
                raise HTTPException(400, detail='Saldo insuficiente. Recarga tu wallet desde Perfil.')
            db.add(WalletMovement(user_id=user.id, tenant_id=payment.tenant_id, branch_id=payment.branch_id,
                payment_id=payment.id, amount=Decimal('-5'), concept='Pago de consulta general'))
        else:
            payment.cashier_id = user.id
        payment.payment_method = method
        ConsultationPaymentService.complete(db, payment, appointment)
        db.commit()
        return {'payment_id': payment.id, 'status': 'COMPLETED'}

    @staticmethod
    def complete(db, payment, appointment=None):
        payment.status = 'COMPLETED'
        payment.paid_at = datetime.now(timezone.utc)
        if appointment:
            appointment.status = 'CONFIRMED'
            from app.models.notification import Notification
            db.add(Notification(user_id=payment.patient_id, tenant_id=payment.tenant_id,
                branch_id=payment.branch_id, title='Cita confirmada', type='CITA_CONFIRMADA',
                reference_id=appointment.id, message='Recibimos el pago de USD 5 de tu consulta general. Tu cita está confirmada.'))
        if payment.purpose == 'WALLET_TOPUP':
            db.add(WalletMovement(user_id=payment.patient_id, tenant_id=payment.tenant_id,
                branch_id=payment.branch_id, payment_id=payment.id, amount=Decimal(str(payment.amount)), concept=payment.concept))

    @staticmethod
    def capture(db, payment):
        from app.services.payment_service import PaymentService
        appointment = None
        if payment.appointment_id:
            appointment = db.query(Appointment).filter_by(id=payment.appointment_id).with_for_update().first()
        payment = db.query(Payment).filter_by(id=payment.id).populate_existing().with_for_update().one()
        if payment.status == 'COMPLETED':
            return PaymentService._map_to_out(payment)
        if payment.status == 'CANCELLED' or (appointment and appointment.status != 'ACCEPTED'):
            raise HTTPException(409, detail='El cobro o la cita ya no está disponible.')
        db.query(User).filter_by(id=payment.patient_id).with_for_update().one()
        try:
            order = PayPalService.get_order(payment.paypal_order_id)
            if order.get('status') != 'COMPLETED':
                if order.get('status') != 'APPROVED':
                    raise HTTPException(409, detail='Completa la aprobación en PayPal y vuelve a verificar.')
                order = PayPalService.capture_order(payment.paypal_order_id)['raw']
            capture = PayPalService.validate_completed_payment(order, payment.amount, 'USD')
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, detail='No se pudo verificar PayPal. Vuelve a verificar el mismo pago; no crees otra recarga.')
        payment.paypal_capture_id = capture['id']
        ConsultationPaymentService.complete(db, payment, appointment)
        db.commit()
        db.refresh(payment)
        return PaymentService._map_to_out(payment)
