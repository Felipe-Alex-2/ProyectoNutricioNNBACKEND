import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, ForeignKey, Numeric, DateTime
from app.database import Base
from app.models.workspace import WorkspaceOwned


class WalletMovement(WorkspaceOwned, Base):
    __tablename__ = 'wallet_movements'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False, index=True)
    branch_id = Column(String(36), ForeignKey('branches.id'), nullable=True, index=True)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False, index=True)
    payment_id = Column(String(36), ForeignKey('payments.id'), nullable=False, unique=True)
    amount = Column(Numeric(12, 2), nullable=False)
    concept = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
