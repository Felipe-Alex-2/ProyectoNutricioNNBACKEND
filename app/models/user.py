import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Integer, Text
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    avatar = Column(Text, nullable=True)  # "preset:<id>" o data URL de la imagen subida

    # Multi-tenant and RBAC
    role_id = Column(String(50), ForeignKey("roles.id", ondelete="RESTRICT"), default="CLIENTE", nullable=False)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)

    branch_id = Column(String(36), ForeignKey('branches.id', ondelete='SET NULL'), nullable=True, index=True)
    workspace_tenant_id = Column(String(36), ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True)
    workspace_branch_id = Column(String(36), ForeignKey('branches.id', ondelete='SET NULL'), nullable=True)
    session_version = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    developer_key_hash = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    role = relationship("Role", back_populates="users")
    tenant = relationship("Tenant", back_populates="users", foreign_keys=[tenant_id])

    @property
    def active_tenant_name(self):
        from sqlalchemy.orm import object_session
        from app.models.tenant import Tenant
        db = object_session(self)
        item = db.get(Tenant, self.active_tenant_id) if db and self.active_tenant_id else None
        return item.name if item else None

    @property
    def active_branch_name(self):
        from sqlalchemy.orm import object_session
        from app.models.company import Branch
        db = object_session(self)
        item = db.get(Branch, self.active_branch_id) if db and self.active_branch_id else None
        return item.name if item else None

    @property
    def active_tenant_id(self):
        return self.workspace_tenant_id if self.role_id == 'SUPERADMIN' else self.tenant_id

    @property
    def active_branch_id(self):
        return self.workspace_branch_id or (self.branch_id if self.role_id != 'SUPERADMIN' else None)

    @property
    def has_custom_developer_key(self) -> bool:
        return bool(self.developer_key_hash)
