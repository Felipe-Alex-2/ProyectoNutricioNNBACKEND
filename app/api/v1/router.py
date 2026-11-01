from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as users_router
from app.api.v1.tenants import router as tenants_router
from app.api.v1.roles import router as roles_router
from app.api.v1.organization_users import router as org_users_router
from app.api.v1.patient_links import router as patient_links_router
from app.api.v1.activity_logs import router as activity_logs_router
from app.api.v1.recipes import router as recipes_router
from app.api.v1.clinical import router as clinical_router
from app.api.v1.subscriptions import router as subscriptions_router
from app.api.v1.payments import router as payments_router
from app.api.v1.appointments import router as appointments_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.backup import router as backup_router
from app.api.v1.reports import router as reports_router
from app.api.v1.plans import router as plans_router
from app.api.v1.chat import router as chat_router
from app.api.v1.food import router as food_router
from app.api.v1.weekly_menu import router as weekly_menu_router
from app.api.v1.automation import router as automation_router
from app.api.v1.consultation_payments import router as consultation_payments_router

api_v1_router = APIRouter(prefix="/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(tenants_router)
api_v1_router.include_router(roles_router)
api_v1_router.include_router(org_users_router)
api_v1_router.include_router(patient_links_router)
api_v1_router.include_router(activity_logs_router)
api_v1_router.include_router(recipes_router)
api_v1_router.include_router(clinical_router)
api_v1_router.include_router(subscriptions_router)
api_v1_router.include_router(payments_router)
api_v1_router.include_router(appointments_router)
api_v1_router.include_router(notifications_router)
api_v1_router.include_router(backup_router)
api_v1_router.include_router(reports_router)
api_v1_router.include_router(plans_router)
api_v1_router.include_router(chat_router)
api_v1_router.include_router(food_router)
api_v1_router.include_router(weekly_menu_router)
api_v1_router.include_router(automation_router)
api_v1_router.include_router(consultation_payments_router)



from app.api.v1.branches import router as branches_router
api_v1_router.include_router(branches_router)
