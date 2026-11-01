from app.models.workspace import WorkspaceOwned
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Appointment(WorkspaceOwned, Base):
    __tablename__ = "appointments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    branch_id = Column(String(36), ForeignKey('branches.id', ondelete='SET NULL'), nullable=True, index=True)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    patient_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    nutritionist_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    scheduled_at = Column(DateTime, nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(String(20), default="PENDING", nullable=False)  # PENDING, CONFIRMED, CANCELLED
    completed_at = Column(DateTime, nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    reminder_sent = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    patient = relationship("User", foreign_keys=[patient_id])
    nutritionist = relationship("User", foreign_keys=[nutritionist_id])
    tenant = relationship("Tenant")
    payment = relationship("Payment", uselist=False, viewonly=True, lazy="selectin")
