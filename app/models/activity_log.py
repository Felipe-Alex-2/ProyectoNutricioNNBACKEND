from app.models.workspace import WorkspaceOwned
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.database import Base

# Zona horaria oficial de Bolivia: UTC-4 (America/La_Paz)
BOLIVIA_TZ = timezone(timedelta(hours=-4))


def bolivia_now():
    return datetime.now(BOLIVIA_TZ)


class ActivityLog(WorkspaceOwned, Base):
    __tablename__ = "activity_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    tenant_id = Column(String(36), ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_email = Column(String(255), nullable=True)
    user_name = Column(String(255), nullable=True)
    action = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=False)
    category = Column(String(50), default="SISTEMA", nullable=False)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=bolivia_now, nullable=False)

    user = relationship("User")
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    branch = relationship("Branch", foreign_keys="[ActivityLog.branch_id]")
