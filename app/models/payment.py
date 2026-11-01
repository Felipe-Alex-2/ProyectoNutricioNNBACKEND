from app.models.workspace import WorkspaceOwned
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Payment(WorkspaceOwned, Base):
    __tablename__ = "payments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    branch_id = Column(String(36), ForeignKey('branches.id', ondelete='SET NULL'), nullable=True, index=True)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    cashier_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    patient_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id"), nullable=True, unique=True)
    purpose = Column(String(30), nullable=False, default="POS", server_default="POS")

    customer_name = Column(String(255), nullable=False)
    customer_email = Column(String(255), nullable=True)
    concept = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)

    # Status: PENDING, COMPLETED, CANCELLED, FAILED
    status = Column(String(50), default="PENDING", nullable=False, index=True)

    # Payment Method: PAYPAL, EFECTIVO
    payment_method = Column(String(50), default="PAYPAL", nullable=False, server_default="PAYPAL", index=True)

    paypal_order_id = Column(String(100), nullable=True, index=True)
    paypal_capture_id = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    paid_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id], lazy="joined")
    cashier = relationship("User", foreign_keys=[cashier_id], lazy="joined")
