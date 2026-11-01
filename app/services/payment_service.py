"""
Point-of-Sale Payment Service.

Manages in-branch charges/payments to clients/patients using PayPal Sandbox.
"""

import io
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, case
from fastapi import HTTPException, status

from app.models.payment import Payment
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.payment import (
    PaymentCreate,
    PaymentOrderCreatedOut,
    PaymentOut,
    PaymentStatsOut,
)
from app.services.paypal_service import PayPalService

logger = logging.getLogger("payments")


class PaymentService:

    @staticmethod
    def _map_to_out(p: Payment) -> PaymentOut:
        return PaymentOut(
            id=p.id,
            patient_id=p.patient_id,
            appointment_id=p.appointment_id,
            purpose=p.purpose,
            tenant_id=p.tenant_id,
            branch_id=p.branch_id,
            tenant_name=p.tenant.name if p.tenant else None,
            cashier_id=p.cashier_id,
            cashier_name=p.cashier.full_name if p.cashier else None,
            customer_name=p.customer_name,
            customer_email=p.customer_email,
            concept=p.concept,
            amount=p.amount,
            currency=p.currency,
            status=p.status,
            payment_method=p.payment_method or "PAYPAL",
            paypal_order_id=p.paypal_order_id,
            paypal_capture_id=p.paypal_capture_id,
            notes=p.notes,
            created_at=p.created_at,
            paid_at=p.paid_at,
        )

    @staticmethod
    def create_payment(
        db: Session,
        cashier: User,
        data: PaymentCreate,
        frontend_url: str = "http://localhost:4200",
    ) -> PaymentOrderCreatedOut:
        """Create a payment record (either immediate EFECTIVO or PENDING PayPal order)."""
        # 1. Verify tenant exists
        tenant = db.query(Tenant).filter(Tenant.id == data.tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sucursal con ID {data.tenant_id} no encontrada",
            )

        from app.services.branch_scope import operation_branch
        branch_id = operation_branch(db, data.tenant_id, data.branch_id, cashier.branch_id if cashier.tenant_id == data.tenant_id else None)
        method = (data.payment_method or "PAYPAL").upper()

        # 2. If Cash (EFECTIVO), record directly as COMPLETED without calling PayPal
        if method == "EFECTIVO":
            now = datetime.now(timezone.utc)
            payment = Payment(
                tenant_id=data.tenant_id,
                branch_id=branch_id,
                cashier_id=cashier.id,
                customer_name=data.customer_name,
                customer_email=data.customer_email,
                concept=data.concept,
                amount=round(data.amount, 2),
                currency=data.currency,
                status="COMPLETED",
                payment_method="EFECTIVO",
                paypal_order_id=None,
                notes=data.notes,
                paid_at=now,
            )
            db.add(payment)
            db.commit()
            db.refresh(payment)

            logger.info(
                f"Cobro en efectivo registrado: ID {payment.id}, Monto ${payment.amount}"
            )

            return PaymentOrderCreatedOut(
                payment_id=payment.id,
                paypal_order_id=None,
                approval_url=None,
                payment_method="EFECTIVO",
                amount=payment.amount,
                currency=payment.currency,
                concept=payment.concept,
                customer_name=payment.customer_name,
            )

        # 3. Call PayPal Sandbox API to create order
        description = f"Cobro Sucursal {tenant.name} - {data.concept} ({data.customer_name})"
        return_url = f"{frontend_url}/paypal-return"
        cancel_url = f"{frontend_url}/dashboard"

        try:
            order_data = PayPalService.create_order(
                amount=data.amount,
                currency=data.currency,
                description=description,
                return_url=return_url,
                cancel_url=cancel_url,
            )
        except Exception as exc:
            logger.error(f"Error creando orden en PayPal Sandbox: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error comunicando con PayPal Sandbox: {str(exc)}",
            )

        # Store Payment in DB as PENDING PayPal order
        payment = Payment(
            tenant_id=data.tenant_id,
            branch_id=branch_id,
            cashier_id=cashier.id,
            customer_name=data.customer_name,
            customer_email=data.customer_email,
            concept=data.concept,
            amount=round(data.amount, 2),
            currency=data.currency,
            status="PENDING",
            payment_method="PAYPAL",
            paypal_order_id=order_data["order_id"],
            notes=data.notes,
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)

        logger.info(
            f"Cobro PayPal creado: ID {payment.id}, PayPal Order {payment.paypal_order_id}, Monto ${payment.amount}"
        )

        return PaymentOrderCreatedOut(
            payment_id=payment.id,
            paypal_order_id=order_data["order_id"],
            approval_url=order_data["approval_url"],
            payment_method="PAYPAL",
            amount=payment.amount,
            currency=payment.currency,
            concept=payment.concept,
            customer_name=payment.customer_name,
        )

    @staticmethod
    def capture_payment(db: Session, paypal_order_id: str) -> PaymentOut:
        """Capture an approved PayPal order and mark the payment as COMPLETED."""
        payment = (
            db.query(Payment)
            .filter(Payment.paypal_order_id == paypal_order_id)
            .first()
        )
        if not payment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontró cobro asociado a la orden PayPal {paypal_order_id}",
            )

        if payment.status == "COMPLETED":
            return PaymentService._map_to_out(payment)

        if payment.purpose in {'CONSULTATION', 'WALLET_TOPUP'}:
            from app.services.consultation_payment_service import ConsultationPaymentService
            return ConsultationPaymentService.capture(db, payment)

        # Capture with PayPal
        try:
            capture_res = PayPalService.capture_order(paypal_order_id)
        except Exception as exc:
            logger.error(f"Error capturando cobro PayPal {paypal_order_id}: {exc}")
            payment.status = "FAILED"
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error capturando orden en PayPal: {str(exc)}",
            )

        if capture_res.get("status") != "COMPLETED" or not capture_res.get("capture_id"):
            raise HTTPException(status_code=502, detail="PayPal no confirmó el cobro.")
        PayPalService.validate_completed_payment(capture_res.get("raw", {}), payment.amount, payment.currency)
        payment.status = "COMPLETED"
        payment.paypal_capture_id = capture_res.get("capture_id")
        payment.paid_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(payment)

        logger.info(f"Cobro completado exitosamente: ID {payment.id}, Monto ${payment.amount}")
        return PaymentService._map_to_out(payment)

    @staticmethod
    def list_payments(
        db: Session,
        tenant_id: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> List[PaymentOut]:
        """List payments filtered by tenant branch and optional status or payment method."""
        query = db.query(Payment).options(joinedload(Payment.tenant), joinedload(Payment.cashier))
        if tenant_id:
            query = query.filter(Payment.tenant_id == tenant_id)
        if status_filter:
            sf = status_filter.upper()
            if sf == "EFECTIVO":
                query = query.filter(Payment.payment_method == "EFECTIVO")
            elif sf == "PAYPAL":
                query = query.filter(Payment.payment_method == "PAYPAL")
            else:
                query = query.filter(Payment.status == status_filter)

        payments = query.order_by(Payment.created_at.desc()).limit(200).all()
        return [PaymentService._map_to_out(p) for p in payments]

    @staticmethod
    def get_payment_stats(db: Session, tenant_id: Optional[str] = None) -> PaymentStatsOut:
        """Calculate summary statistics for a given branch or all branches."""
        query = db.query(Payment).options(joinedload(Payment.tenant), joinedload(Payment.cashier))
        if tenant_id:
            query = query.filter(Payment.tenant_id == tenant_id)

        query = query.filter(Payment.purpose != "WALLET_TOPUP")
        totals = query.with_entities(
            func.coalesce(func.sum(case((Payment.status == "COMPLETED", Payment.amount), else_=0)), 0),
            func.count(Payment.id),
            func.coalesce(func.sum(case((Payment.status == "COMPLETED", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Payment.status == "PENDING", 1), else_=0)), 0),
        ).one()
        return PaymentStatsOut(total_collected=round(float(totals[0]), 2), total_count=totals[1], completed_count=totals[2], pending_count=totals[3])

    @staticmethod
    def cancel_payment(db: Session, payment_id: str) -> PaymentOut:
        """Cancel a pending payment."""
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cobro no encontrado",
            )
        if payment.status == "COMPLETED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede cancelar un cobro ya completado.",
            )

        payment.status = "CANCELLED"
        db.commit()
        db.refresh(payment)
        return PaymentService._map_to_out(payment)

    @staticmethod
    def generate_payment_pdf(db: Session, payment_id: str) -> Tuple[io.BytesIO, str]:
        """Genera un archivo PDF formal con el Detalle de Pago (comprobante / recibo)."""
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cobro no encontrado",
            )

        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        stream = io.BytesIO()
        doc = SimpleDocTemplate(
            stream,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()

        brand_style = ParagraphStyle(
            name="BrandTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=colors.HexColor("#206443"),
            spaceAfter=2,
        )
        brand_sub = ParagraphStyle(
            name="BrandSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=colors.HexColor("#64748b"),
        )
        doc_title_style = ParagraphStyle(
            name="DocTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            textColor=colors.HexColor("#1e293b"),
            alignment=2,
            spaceAfter=2,
        )
        doc_folio_style = ParagraphStyle(
            name="DocFolio",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.HexColor("#206443"),
            alignment=2,
        )
        section_heading = ParagraphStyle(
            name="SectionHeading",
            parent=styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=colors.HexColor("#206443"),
            spaceAfter=4,
        )
        body_style = ParagraphStyle(
            name="BodyText",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            textColor=colors.HexColor("#334155"),
            leading=12,
        )
        table_head_style = ParagraphStyle(
            name="TableHead",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.white,
            alignment=1,
        )
        table_cell_style = ParagraphStyle(
            name="TableCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            textColor=colors.HexColor("#1e293b"),
            alignment=1,
        )
        table_cell_left = ParagraphStyle(
            name="TableCellLeft",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            textColor=colors.HexColor("#1e293b"),
        )
        total_style = ParagraphStyle(
            name="TotalText",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=colors.HexColor("#206443"),
            alignment=2,
        )
        footer_style = ParagraphStyle(
            name="FooterText",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=colors.HexColor("#94a3b8"),
            alignment=1,
            leading=10,
        )

        elements = []

        # 1. Header institucional
        header_table_data = [
            [
                Paragraph("<b>NUTRISALUD</b>", brand_style),
                Paragraph("<b>DETALLE DE PAGO</b>", doc_title_style),
            ],
            [
                Paragraph("Plataforma Integral de Gestion Nutricional y Clinica", brand_sub),
                Paragraph(f"Comprobante: #PAG-{payment.id[:8].upper()}", doc_folio_style),
            ],
        ]
        header_table = Table(header_table_data, colWidths=[300, 240])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 8))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#206443"), spaceAfter=10))

        # 2. Informacion de la Sucursal e Informacion del Cobro en dos columnas
        tenant_name = payment.tenant.name if payment.tenant else "Sucursal General"
        tenant_code = payment.tenant.code if payment.tenant else "N/A"
        tenant_phone = payment.tenant.phone if payment.tenant and payment.tenant.phone else "No registrado"
        tenant_email = payment.tenant.email if payment.tenant and payment.tenant.email else "No registrado"
        tenant_address = payment.tenant.address if payment.tenant and payment.tenant.address else "No registrada"

        cashier_name = payment.cashier.full_name if payment.cashier else "Administracion"
        created_str = payment.created_at.strftime("%d/%m/%Y %H:%M") if payment.created_at else "-"
        paid_str = payment.paid_at.strftime("%d/%m/%Y %H:%M") if payment.paid_at else "Pendiente de Confirmacion"

        status_text = {
            "COMPLETED": "PAGADO / COMPLETADO",
            "PENDING": "PENDIENTE DE PAGO",
            "CANCELLED": "CANCELADO",
            "FAILED": "FALLIDO",
        }.get(payment.status, payment.status)

        method_text = {"EFECTIVO": "Efectivo (Caja Local)", "WALLET": "Wallet NutriSalud", "PENDING": "Por elegir"}.get(payment.payment_method, "PayPal Sandbox")

        sucursal_info = f"""
        <b>Nombre:</b> {tenant_name}<br/>
        <b>Codigo Sucursal:</b> {tenant_code}<br/>
        <b>Direccion:</b> {tenant_address}<br/>
        <b>Telefono:</b> {tenant_phone}<br/>
        <b>Correo:</b> {tenant_email}
        """

        transaccion_info = f"""
        <b>Fecha de Registro:</b> {created_str}<br/>
        <b>Fecha de Pago:</b> {paid_str}<br/>
        <b>Metodo de Pago:</b> {method_text}<br/>
        <b>Estado del Cobro:</b> {status_text}<br/>
        <b>Referencia / Orden:</b> {payment.paypal_order_id or 'Caja Local'}<br/>
        <b>Atendido por:</b> {cashier_name}
        """

        two_cols_data = [
            [
                Paragraph("<b>DATOS DE LA SUCURSAL</b>", section_heading),
                Paragraph("<b>DATOS DE LA TRANSACCION</b>", section_heading),
            ],
            [
                Paragraph(sucursal_info, body_style),
                Paragraph(transaccion_info, body_style),
            ],
        ]
        two_cols_table = Table(two_cols_data, colWidths=[270, 270])
        two_cols_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 1), (0, 1), colors.HexColor("#f8fafc")),
            ("BACKGROUND", (1, 1), (1, 1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 1), (0, 1), 0.5, colors.HexColor("#e2e8f0")),
            ("BOX", (1, 1), (1, 1), 0.5, colors.HexColor("#e2e8f0")),
            ("PADDING", (0, 1), (-1, -1), 8),
        ]))
        elements.append(two_cols_table)
        elements.append(Spacer(1, 12))

        # 3. Informacion del Paciente / Cliente
        client_info = f"""
        <b>Nombre del Paciente / Cliente:</b> {payment.customer_name}<br/>
        <b>Correo Electronico:</b> {payment.customer_email or 'No especificado'}
        """
        client_table_data = [
            [Paragraph("<b>DATOS DEL CLIENTE</b>", section_heading)],
            [Paragraph(client_info, body_style)],
        ]
        client_table = Table(client_table_data, colWidths=[540])
        client_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 1), (0, 1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 1), (0, 1), 0.5, colors.HexColor("#e2e8f0")),
            ("PADDING", (0, 1), (0, 1), 8),
        ]))
        elements.append(client_table)
        elements.append(Spacer(1, 14))

        # 4. Tabla de Detalle del Cobro
        detail_data = [
            [
                Paragraph("N°", table_head_style),
                Paragraph("Concepto / Descripcion del Servicio", table_head_style),
                Paragraph("Metodo", table_head_style),
                Paragraph("Estado", table_head_style),
                Paragraph("Monto", table_head_style),
            ],
            [
                Paragraph("1", table_cell_style),
                Paragraph(f"<b>{payment.concept}</b>", table_cell_left),
                Paragraph(payment.payment_method or "PAYPAL", table_cell_style),
                Paragraph(status_text, table_cell_style),
                Paragraph(f"${payment.amount:.2f} {payment.currency}", table_cell_style),
            ],
            [
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph("<b>TOTAL:</b>", ParagraphStyle(name="TotalLabel", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10, textColor=colors.HexColor("#206443"), alignment=2)),
                Paragraph(f"<b>${payment.amount:.2f} {payment.currency}</b>", total_style),
            ],
        ]

        detail_table = Table(detail_data, colWidths=[30, 240, 90, 100, 80])
        detail_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#206443")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, 1), 0.5, colors.HexColor("#cbd5e1")),
            ("BACKGROUND", (0, 1), (-1, 1), colors.white),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("BACKGROUND", (3, 2), (-1, 2), colors.HexColor("#f1f5f9")),
            ("LINEABOVE", (0, 2), (-1, 2), 1, colors.HexColor("#206443")),
        ]))
        elements.append(detail_table)
        elements.append(Spacer(1, 14))

        # 5. Notas u observaciones si existen
        if payment.notes:
            notes_p = Paragraph(f"<b>Notas u Observaciones:</b> {payment.notes}", body_style)
            elements.append(notes_p)
            elements.append(Spacer(1, 10))

        # 6. Pie de pagina
        elements.append(Spacer(1, 16))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1"), spaceAfter=8))
        elements.append(Paragraph("Este comprobante de detalle de pago es un documento oficial emitido por NutriSalud Platform.<br/>Para cualquier consulta respecto a esta transaccion, comuniquese con la sucursal emisora.", footer_style))

        doc.build(elements)
        stream.seek(0)
        filename = f"Detalle_Pago_{payment.id[:8].upper()}.pdf"
        return stream, filename
