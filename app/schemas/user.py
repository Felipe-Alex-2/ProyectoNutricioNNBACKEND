import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator


def validate_password_strength(password: str) -> str:
    if len(password) < 8:
        raise ValueError("La contraseña debe contener al menos 8 caracteres")
    if not re.search(r"[A-Z]", password):
        raise ValueError("La contraseña debe incluir al menos una letra mayúscula")
    if not re.search(r"[a-z]", password):
        raise ValueError("La contraseña debe incluir al menos una letra minúscula")
    if not re.search(r"[0-9]", password):
        raise ValueError("La contraseña debe incluir al menos un número")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError("La contraseña debe incluir al menos un carácter especial")
    return password


class UserBase(BaseModel):

    email: EmailStr = Field(..., description="Correo electrónico válido")
    full_name: str = Field(..., min_length=2, max_length=250, description="Nombre completo (mínimo 2, máximo 250 caracteres)")
    phone: Optional[str] = Field(None, min_length=7, max_length=25, description="Teléfono de contacto")

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("El nombre completo debe tener al menos 2 caracteres")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) < 7:
                raise ValueError("El teléfono debe contener al menos 7 dígitos")
        return v


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128, description="Contraseña de acceso (mínimo 8 caracteres)")
    role_id: Optional[str] = Field("CLIENTE", description="Rol asignado al usuario")
    tenant_id: Optional[str] = Field(None, description="ID de la organización asociada")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


class OrganizationUserCreate(UserBase):
    branch_id: Optional[str] = None
    password: str = Field(..., min_length=8, max_length=128, description="Contraseña de acceso (mínimo 8 caracteres)")
    role_id: str = Field(..., min_length=2, max_length=50, description="Rol del usuario (ej: NUTRICIONISTA, CLIENTE, ADMIN_ORGANIZATION)")
    tenant_id: str = Field(..., min_length=1, description="ID de la organización o clínica asociada")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


class UserUpdate(BaseModel):
    branch_id: Optional[str] = None
    full_name: Optional[str] = Field(None, min_length=2, max_length=250, description="Nombre completo (máximo 250 caracteres)")
    email: Optional[EmailStr] = Field(None, description="Correo electrónico")
    phone: Optional[str] = Field(None, min_length=7, max_length=25, description="Teléfono de contacto")
    password: Optional[str] = Field(None, min_length=8, max_length=128, description="Nueva contraseña")
    role_id: Optional[str] = Field(None, description="Rol del usuario")
    tenant_id: Optional[str] = Field(None, description="ID de la organización")
    is_active: Optional[bool] = Field(None, description="Estado de actividad")
    avatar: Optional[str] = Field(None, max_length=400000, description="Avatar: 'preset:<id>' o imagen en data URL; vacío lo quita")
    developer_key: Optional[str] = Field(None, min_length=4, max_length=128, description="Llave personalizada para acceso a bitácora")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return validate_password_strength(v)
        return v


    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if len(v) < 2:
                raise ValueError("El nombre completo debe tener al menos 2 caracteres")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) < 7:
                raise ValueError("El teléfono debe contener al menos 7 dígitos")
        return v


    @field_validator("avatar")
    @classmethod
    def validate_avatar(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if v and not re.fullmatch(r"preset:(hombre-lentes|hombre|mujer)|data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+", v):
            raise ValueError("Avatar no válido: usa uno predeterminado o una imagen PNG, JPG o WEBP.")
        return v


class DeveloperKeyVerifyRequest(BaseModel):
    developer_key: str = Field(..., min_length=1, description="Llave del administrador o desarrollador para la bitácora")


class DeveloperKeyVerifyResponse(BaseModel):
    valid: bool
    message: str


class UserResponse(UserBase):
    active_tenant_name: Optional[str] = None
    active_branch_name: Optional[str] = None
    active_tenant_id: Optional[str] = None
    active_branch_id: Optional[str] = None
    branch_id: Optional[str] = None
    id: str
    role_id: str
    tenant_id: Optional[str] = None
    is_active: bool
    is_verified: bool
    avatar: Optional[str] = None
    has_custom_developer_key: Optional[bool] = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)



class ProfileUpdate(UserUpdate):
    """Personal profile fields; administrative fields are explicitly rejected."""
    @field_validator("role_id", "tenant_id", "branch_id", "is_active", mode="before")
    @classmethod
    def reject_administrative_fields(cls, value):
        raise ValueError("Los campos administrativos no se pueden modificar desde el perfil.")


class WorkspaceUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: Optional[str] = None
    branch_id: Optional[str] = None
