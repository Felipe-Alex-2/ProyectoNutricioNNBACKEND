from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class AppointmentCreate(BaseModel):
    branch_id: Optional[str] = None
    nutritionist_id: str = Field(..., description="ID del nutricionista asignado")
    scheduled_at: datetime = Field(..., description="Fecha y hora de la cita programada")
    reason: Optional[str] = Field(None, description="Motivo opcional de la consulta")
    tenant_id: Optional[str] = Field(None, description="ID de la clínica o sucursal")


class AppointmentCancel(BaseModel):
    cancellation_reason: Optional[str] = Field(None, description="Motivo de la cancelación de la cita")


class AppointmentOut(BaseModel):
    payment_status: str = "UNRECORDED"
    payment_preference: Optional[str] = None
    payment_method: Optional[str] = None
    payment_id: Optional[str] = None
    branch_id: Optional[str] = None
    completed_at: Optional[datetime] = None
    id: str
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None
    patient_id: str
    patient_name: Optional[str] = None
    patient_email: Optional[str] = None
    nutritionist_id: str
    nutritionist_name: Optional[str] = None
    scheduled_at: datetime
    reason: Optional[str] = None
    status: str
    cancellation_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NutritionistOut(BaseModel):
    id: str
    full_name: str
    email: str
    phone: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
