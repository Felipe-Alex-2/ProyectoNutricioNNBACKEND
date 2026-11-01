from datetime import datetime, timezone, timedelta
from typing import List, Optional
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy.orm import joinedload
from app.core.authorization import require_tenant
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.appointment import Appointment
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.appointment import AppointmentCreate, AppointmentOut, NutritionistOut
from app.services.notification_service import NotificationService


class AppointmentService:
    @staticmethod
    def create_appointment(db: Session, current_user: User, data: AppointmentCreate, idempotency_key: Optional[str] = None) -> AppointmentOut:
        # Serialize schedules for both participants on PostgreSQL. A retry returns the same record.
        participants = db.query(User).filter(User.id.in_([current_user.id, data.nutritionist_id])).order_by(User.id).with_for_update().all()
        nutritionist = next((user for user in participants if user.id == data.nutritionist_id and user.role_id == "NUTRICIONISTA" and user.is_active), None)
        if not nutritionist:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Nutricionista no encontrado",
            )

        tenant_id = nutritionist.tenant_id
        require_tenant(current_user, tenant_id)
        if data.tenant_id is not None and data.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="La clínica no coincide con el especialista.")
        appointment_id = str(uuid5(NAMESPACE_URL, f"nutri-appointment:{current_user.id}:{idempotency_key}")) if idempotency_key else None
        if appointment_id:
            existing = db.get(Appointment, appointment_id)
            if existing:
                requested_time = data.scheduled_at.replace(tzinfo=timezone.utc) if data.scheduled_at.tzinfo is None else data.scheduled_at.astimezone(timezone.utc)
                existing_time = existing.scheduled_at.replace(tzinfo=timezone.utc) if existing.scheduled_at.tzinfo is None else existing.scheduled_at.astimezone(timezone.utc)
                if existing.nutritionist_id != data.nutritionist_id or requested_time != existing_time or existing.reason != data.reason:
                    raise HTTPException(status_code=409, detail="La clave de reintento corresponde a otra cita.")
                return AppointmentService._to_out(existing)

        # Asegurar zona horaria UTC si es naive
        scheduled_at = data.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        # Validar solapamiento: cada cita dura 30 minutos.
        # Si el MISMO nutricionista o el MISMO paciente ya tiene una cita (PENDING o CONFIRMED) dentro de los 30 minutos:
        overlap_start = scheduled_at - timedelta(minutes=29, seconds=59)
        overlap_end = scheduled_at + timedelta(minutes=29, seconds=59)

        conflicting = (
            db.query(Appointment)
            .filter(
                Appointment.nutritionist_id == data.nutritionist_id,
                Appointment.status.in_(["PENDING", "ACCEPTED", "CONFIRMED"]),
                Appointment.scheduled_at >= overlap_start,
                Appointment.scheduled_at <= overlap_end,
            )
            .first()
        )
        if conflicting:
            conf_time = conflicting.scheduled_at.strftime("%H:%M")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El especialista ya tiene una cita agendada o pendiente a las {conf_time}. Cada consulta tiene una duración de 30 minutos. Por favor selecciona otro horario.",
            )

        patient_conflicting = (
            db.query(Appointment)
            .filter(
                Appointment.patient_id == current_user.id,
                Appointment.status.in_(["PENDING", "ACCEPTED", "CONFIRMED"]),
                Appointment.scheduled_at >= overlap_start,
                Appointment.scheduled_at <= overlap_end,
            )
            .first()
        )
        if patient_conflicting:
            conf_time = patient_conflicting.scheduled_at.strftime("%H:%M")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya tienes una cita agendada o pendiente a las {conf_time} en ese intervalo de 30 minutos. Por favor selecciona otro horario.",
            )

        from app.services.branch_scope import operation_branch
        branch_id = operation_branch(db, tenant_id, data.branch_id, current_user.branch_id if current_user.tenant_id == tenant_id else None)
        appointment = Appointment(
            branch_id=branch_id,
            **({"id": appointment_id} if appointment_id else {}),
            tenant_id=tenant_id,
            patient_id=current_user.id,
            nutritionist_id=data.nutritionist_id,
            scheduled_at=scheduled_at,
            reason=data.reason,
            status="PENDING",
        )
        db.add(appointment)
        db.commit()
        db.refresh(appointment)

        # 1. Notificar al nutricionista
        formatted_date = appointment.scheduled_at.strftime("%d/%m/%Y a las %H:%M")
        NotificationService.create_notification(
            db=db,
            user_id=nutritionist.id,
            title="Nueva Cita Pendiente",
            message=f"{current_user.full_name} agendó una cita para el {formatted_date}. Motivo: {appointment.reason or 'Consulta general'}.",
            type="CITA_CREADA",
            reference_id=appointment.id,
            tenant_id=tenant_id,
        )

        # 2. Notificar a los administradores de la organización
        if tenant_id:
            org_admins = (
                db.query(User)
                .filter(
                    User.tenant_id == tenant_id,
                    User.role_id.in_(["ADMIN_ORGANIZATION", "SUPERADMIN"]),
                    User.id != current_user.id,
                    User.id != nutritionist.id,
                )
                .all()
            )
            for admin in org_admins:
                NotificationService.create_notification(
                    db=db,
                    user_id=admin.id,
                    title="Nueva Cita Agendada",
                    message=f"Nueva solicitud de cita: {current_user.full_name} con {nutritionist.full_name} para el {formatted_date}.",
                    type="CITA_CREADA",
                    reference_id=appointment.id,
                    tenant_id=tenant_id,
                )

        return AppointmentService._to_out(appointment)

    @staticmethod
    def list_appointments(
        db: Session,
        current_user: User,
        status_filter: Optional[str] = None,
        tenant_id: Optional[str] = None,
        nutritionist_id: Optional[str] = None,
    ) -> List[AppointmentOut]:
        query = db.query(Appointment).options(joinedload(Appointment.patient), joinedload(Appointment.nutritionist), joinedload(Appointment.tenant))
        if current_user.role_id not in {"SUPERADMIN", "ADMIN_ORGANIZATION", "NUTRICIONISTA", "CLIENTE"}:
            raise HTTPException(status_code=403, detail="Rol sin acceso a citas.")
        if current_user.role_id in {"ADMIN_ORGANIZATION", "NUTRICIONISTA"}:
            require_tenant(current_user, current_user.tenant_id)

        if current_user.role_id == "CLIENTE":
            query = query.filter(Appointment.patient_id == current_user.id)
        elif current_user.role_id == "NUTRICIONISTA":
            query = query.filter(Appointment.nutritionist_id == current_user.id)
            if current_user.tenant_id:
                query = query.filter(Appointment.tenant_id == current_user.tenant_id)
        elif current_user.role_id == "ADMIN_ORGANIZATION":
            if current_user.tenant_id:
                query = query.filter(Appointment.tenant_id == current_user.tenant_id)
        elif current_user.role_id == "SUPERADMIN":
            if tenant_id:
                query = query.filter(Appointment.tenant_id == tenant_id)

        if nutritionist_id and nutritionist_id.upper() != "ALL":
            query = query.filter(Appointment.nutritionist_id == nutritionist_id)

        if status_filter and status_filter.upper() != "ALL":
            query = query.filter(Appointment.status == status_filter.upper())

        appointments = query.order_by(Appointment.scheduled_at.desc()).all()
        return [AppointmentService._to_out(a) for a in appointments]

    @staticmethod
    def confirm_appointment(db: Session, appointment_id: str, current_user: User) -> AppointmentOut:
        appointment = db.query(Appointment).filter(Appointment.id == appointment_id).with_for_update().first()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cita no encontrada",
            )

        # Validar permisos
        if current_user.role_id not in ["SUPERADMIN", "ADMIN_ORGANIZATION", "NUTRICIONISTA"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para confirmar esta cita",
            )

        require_tenant(current_user, appointment.tenant_id)
        if current_user.role_id == "NUTRICIONISTA" and appointment.nutritionist_id != current_user.id:
            raise HTTPException(status_code=403, detail="Solo puedes confirmar tus propias citas.")

        db.query(User).filter(User.id == appointment.nutritionist_id).with_for_update().first()

        # Validar solapamiento con citas CONFIRMED del mismo especialista (duración de 30 min)
        overlap_start = appointment.scheduled_at - timedelta(minutes=29, seconds=59)
        overlap_end = appointment.scheduled_at + timedelta(minutes=29, seconds=59)

        conflicting = (
            db.query(Appointment)
            .filter(
                Appointment.id != appointment.id,
                Appointment.nutritionist_id == appointment.nutritionist_id,
                Appointment.status.in_(["ACCEPTED", "CONFIRMED"]),
                Appointment.scheduled_at >= overlap_start,
                Appointment.scheduled_at <= overlap_end,
            )
            .first()
        )
        if conflicting:
            conf_time = conflicting.scheduled_at.strftime("%H:%M")
            nutri_name = appointment.nutritionist.full_name if appointment.nutritionist else "El especialista"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{nutri_name} ya se encuentra ocupado con una cita confirmada a las {conf_time} (rango de 30 minutos). No es posible confirmar dos citas simultáneas para el mismo doctor.",
            )

        if appointment.status != "PENDING":
            raise HTTPException(status_code=400, detail="Solo se pueden confirmar citas pendientes.")
        appointment.status = "ACCEPTED"
        from app.services.consultation_payment_service import ConsultationPaymentService
        ConsultationPaymentService.ensure_charge(db, appointment)
        appointment.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(appointment)

        # Notificar al paciente
        formatted_date = appointment.scheduled_at.strftime("%d/%m/%Y a las %H:%M")
        nutri_name = appointment.nutritionist.full_name if appointment.nutritionist else "tu especialista"
        NotificationService.create_notification(
            db=db,
            user_id=appointment.patient_id,
            title="Cita aceptada: pago pendiente",
            message=f"Tu cita programada para el {formatted_date} con {nutri_name} ha sido aceptada. Paga la consulta general de USD 5 para confirmarla.",
            type="CITA_ACEPTADA",
            reference_id=appointment.id,
            tenant_id=appointment.tenant_id,
        )

        return AppointmentService._to_out(appointment)

    @staticmethod
    def cancel_appointment(
        db: Session,
        appointment_id: str,
        reason: Optional[str],
        current_user: User,
    ) -> AppointmentOut:
        appointment = db.query(Appointment).filter(Appointment.id == appointment_id).with_for_update().first()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cita no encontrada",
            )

        if current_user.role_id == "CLIENTE":
            if appointment.patient_id != current_user.id:
                raise HTTPException(status_code=403, detail="Solo puedes cancelar tus propias citas.")
        elif current_user.role_id in {"SUPERADMIN", "ADMIN_ORGANIZATION", "NUTRICIONISTA"}:
            require_tenant(current_user, appointment.tenant_id)
            if current_user.role_id == "NUTRICIONISTA" and appointment.nutritionist_id != current_user.id:
                raise HTTPException(status_code=403, detail="Solo puedes cancelar tus propias citas.")
        else:
            raise HTTPException(status_code=403, detail="No puedes cancelar esta cita.")

        if appointment.status == "COMPLETED":
            raise HTTPException(status_code=400, detail="Una cita atendida no se puede cancelar.")
        from app.models.payment import Payment
        charge = db.query(Payment).filter_by(appointment_id=appointment.id).first()
        if charge and charge.status == 'COMPLETED':
            raise HTTPException(409, detail="La cita está pagada. Solicita a la administración su cancelación y devolución antes de continuar.")
        if charge and charge.paypal_order_id:
            raise HTTPException(409, detail="Hay un pago PayPal iniciado. Verifica su resultado antes de cancelar la cita.")
        if charge:
            charge.status = 'CANCELLED'
        appointment.status = "CANCELLED"
        appointment.cancellation_reason = reason or "Cancelada por el administrador/especialista"
        appointment.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(appointment)

        formatted_date = appointment.scheduled_at.strftime("%d/%m/%Y a las %H:%M")

        # Si canceló el admin o nutri -> notificar al paciente
        if current_user.id != appointment.patient_id:
            NotificationService.create_notification(
                db=db,
                user_id=appointment.patient_id,
                title="Cita Cancelada",
                message=f"Tu cita para el {formatted_date} ha sido cancelada. Motivo: {appointment.cancellation_reason}.",
                type="CITA_CANCELADA",
                reference_id=appointment.id,
                tenant_id=appointment.tenant_id,
            )
        else:
            # Si canceló el paciente -> notificar al nutri
            NotificationService.create_notification(
                db=db,
                user_id=appointment.nutritionist_id,
                title="Cita Cancelada por Paciente",
                message=f"El paciente {current_user.full_name} canceló la cita del {formatted_date}. Motivo: {appointment.cancellation_reason}.",
                type="CITA_CANCELADA",
                reference_id=appointment.id,
                tenant_id=appointment.tenant_id,
            )

        return AppointmentService._to_out(appointment)

    @staticmethod
    def get_available_nutritionists(
        db: Session,
        current_user: User,
        tenant_id: Optional[str] = None,
    ) -> List[NutritionistOut]:
        target_tenant = tenant_id or current_user.tenant_id
        require_tenant(current_user, target_tenant)
        query = db.query(User).filter(User.role_id == "NUTRICIONISTA", User.is_active == True)
        if target_tenant:
            query = query.filter(User.tenant_id == target_tenant)
        elif current_user.role_id != "SUPERADMIN":
            query = query.filter(User.tenant_id == current_user.tenant_id)

        nutritionists = query.all()
        return [
            NutritionistOut(
                id=n.id,
                full_name=n.full_name,
                email=n.email,
                phone=n.phone,
            )
            for n in nutritionists
        ]

    @staticmethod
    def _to_out(a: Appointment) -> AppointmentOut:
        payment = a.payment
        method = payment.payment_method if payment else None
        return AppointmentOut(
            payment_status='PAID' if payment and payment.status == 'COMPLETED' else 'UNPAID' if payment else 'UNRECORDED',
            payment_preference='PRESENCIAL' if method in {'PRESENCIAL', 'EFECTIVO'} else 'ONLINE' if method in {'ONLINE', 'PAYPAL', 'WALLET'} else None,
            payment_method=method,
            payment_id=payment.id if payment else None,
            id=a.id,
            tenant_id=a.tenant_id,
            branch_id=a.branch_id,
            tenant_name=a.tenant.name if a.tenant else None,
            patient_id=a.patient_id,
            patient_name=a.patient.full_name if a.patient else None,
            patient_email=a.patient.email if a.patient else None,
            nutritionist_id=a.nutritionist_id,
            nutritionist_name=a.nutritionist.full_name if a.nutritionist else None,
            scheduled_at=a.scheduled_at,
            reason=a.reason,
            status=a.status,
            cancellation_reason=a.cancellation_reason,
            created_at=a.created_at,
            updated_at=a.updated_at,
            completed_at=a.completed_at,
        )
