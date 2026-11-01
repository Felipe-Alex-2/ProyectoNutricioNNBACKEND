from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class PaymentCreate(BaseModel):
    branch_id: Optional[str] = None
    tenant_id: str = Field(..., description="ID de la sucursal u organización")
    customer_name: str = Field(..., min_length=2, description="Nombre del cliente o paciente")
    customer_email: Optional[str] = Field(None, description="Correo electrónico del cliente")
    concept: str = Field(..., min_length=2, description="Concepto del cobro (ej. Consulta, Plan Nutricional, etc.)")
    amount: float = Field(..., gt=0, description="Monto total a cobrar")
    currency: str = Field("USD", description="Moneda (USD)")
    payment_method: str = Field("PAYPAL", description="Método de cobro: 'PAYPAL' o 'EFECTIVO'")
    notes: Optional[str] = Field(None, description="Observaciones adicionales de caja")


class PaymentCaptureRequest(BaseModel):
    paypal_order_id: str = Field(..., description="ID de orden retornado por PayPal")


class PaymentOut(BaseModel):
    patient_id: Optional[str] = None
    appointment_id: Optional[str] = None
    purpose: str = "POS"
    branch_id: Optional[str] = None
    id: str
    tenant_id: str
    tenant_name: Optional[str] = None
    cashier_id: Optional[str] = None
    cashier_name: Optional[str] = None
    customer_name: str
    customer_email: Optional[str] = None
    concept: str
    amount: float
    currency: str
    status: str
    payment_method: str = "PAYPAL"
    paypal_order_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    paid_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PaymentOrderCreatedOut(BaseModel):
    payment_id: str
    paypal_order_id: Optional[str] = None
    approval_url: Optional[str] = None
    payment_method: str = "PAYPAL"
    amount: float
    currency: str
    concept: str
    customer_name: str


class PaymentStatsOut(BaseModel):
    total_collected: float = 0.0
    total_count: int = 0
    completed_count: int = 0
    pending_count: int = 0
