from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator


class TenantBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=250, description="Nombre de la clínica u organización (máximo 250 caracteres)")
    code: str = Field(..., min_length=2, max_length=50, description="Código único identificador")
    phone: Optional[str] = Field(None, min_length=7, max_length=25, description="Teléfono de contacto")
    email: Optional[EmailStr] = Field(None, description="Correo electrónico de contacto")
    address: Optional[str] = Field(None, max_length=255, description="Dirección física")
    department_code: Optional[str] = Field(None, min_length=2, max_length=2)
    logo_url: Optional[str] = Field(None, max_length=500, description="URL del logotipo")
    description: Optional[str] = Field(None, max_length=500, description="Descripción de la clínica")
    is_active: bool = Field(True, description="Estado activo de la organización")

    @field_validator("name", "code", mode='before')
    @classmethod
    def validate_not_blank(cls, v: str) -> str:
        v = ' '.join(v.split())
        if not v:
            raise ValueError("El campo no puede estar vacío ni contener solo espacios")
        return v


class TenantCreate(TenantBase):
    @field_validator('code','initial_branch_code')
    @classmethod
    def valid_code(cls, value):
        import re
        value = value.strip().upper()
        if not re.fullmatch(r'[A-Z0-9_-]{2,50}', value):
            raise ValueError('El código requiere de 2 a 50 letras, números, guiones o guiones bajos, sin espacios.')
        return value
    initial_province_code: Optional[str] = Field(None, min_length=4, max_length=4)
    initial_municipality_code: Optional[str] = Field(None, min_length=6, max_length=6)
    administrator_id: Optional[str] = None
    initial_branch_name: str = Field('Sucursal principal', min_length=2, max_length=250)
    initial_branch_code: str = Field('PRINCIPAL', min_length=2, max_length=50)

    @field_validator('initial_branch_name', 'initial_branch_code', mode='before')
    @classmethod
    def nonblank_branch(cls, value):
        value = ' '.join(value.split())
        if len(value) < 2: raise ValueError('La sucursal requiere nombre y código válidos.')
        return value


class TenantUpdate(BaseModel):
    department_code: Optional[str] = Field(None, min_length=2, max_length=2)
    name: Optional[str] = Field(None, min_length=2, max_length=250, description="Nombre de la clínica")
    phone: Optional[str] = Field(None, min_length=7, max_length=25, description="Teléfono de contacto")
    email: Optional[EmailStr] = Field(None, description="Correo electrónico")
    address: Optional[str] = Field(None, max_length=255, description="Dirección física")
    logo_url: Optional[str] = Field(None, max_length=500, description="URL del logotipo")
    description: Optional[str] = Field(None, max_length=500, description="Descripción")
    is_active: Optional[bool] = Field(None, description="Estado activo")


class TenantResponse(TenantBase):
    id: str
    created_at: datetime
    updated_at: datetime
    users_count: Optional[int] = 0
    administrator_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

