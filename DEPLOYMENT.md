# Ejecución y despliegue

## Inicio local sin Railway

Desde `backend`, ejecuta `.\.venv\Scripts\python.exe run_local.py`.
Genera `.env.local` (ignorado por Git), crea/migra `nutricion_local.db`
con SQLite y arranca http://127.0.0.1:8000 con recarga automática.
La base local empieza vacía y no importa los datos de Railway. Este comando
solo acepta SQLite o un PostgreSQL en localhost. No modifica `.env`.

Para crear tu administrador local, con el servidor detenido:
`.\.venv\Scripts\python.exe run_local.py create-admin --email TU_CORREO --name Felipe`.
La contraseña se solicita de forma oculta. Después vuelve a iniciar el servidor.
Los registros públicos crean pacientes.

En otra terminal, desde `web`, ejecuta `npm.cmd start` y abre
http://localhost:4200. El perfil de desarrollo usa http://localhost:8000/api/v1.
SQLite sirve para trabajar localmente; valida también en PostgreSQL antes de
desplegar. PayPal, Gemini, correo y Firebase requieren sus servicios y claves;
este arranque no los simula ni recupera datos remotos.

Python 3.12. Instala con `python -m pip install -r requirements.txt` (versiones verificadas fijadas directamente en requirements.txt).

## Desarrollo aislado

Configura un PostgreSQL de desarrollo en DATABASE_URL; no reutilices la URL de producción. Usa ENVIRONMENT=development. Ejecuta `python prepare_db.py` y luego `python run.py`. El servidor no crea tablas ni modifica roles al arrancar. Las pruebas `python -m pytest tests -q` usan SQLite y credenciales aisladas.

Para crear el primer administrador, ejecuta `python create_admin.py --help`. El comando pide una contraseña de forma oculta y no transforma cuentas existentes. El registro público crea pacientes.

## Railway

railway.toml prepara el esquema con `python prepare_db.py` antes del despliegue y comprueba `/health`. Documentación: https://docs.railway.com/config-as-code y https://docs.railway.com/deployments/pre-deploy-command.

Variables necesarias:

- ENVIRONMENT=production
- DATABASE_URL: referencia al PostgreSQL correspondiente.
- SECRET_KEY: secreto aleatorio propio de al menos 32 caracteres.
- BACKUP_ENCRYPTION_KEY: secreto aleatorio distinto de al menos 32 caracteres. Conservar fuera del servicio para recuperar respaldos; no cambiarlo al rotar JWT.
- CORS_ORIGINS: únicamente los orígenes web autorizados, separados por comas.
- FRONTEND_URL: URL HTTPS pública de la web.
- PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_BASE_URL: solo en backend, entorno coherente (Sandbox o Live).
- GEMINI_API_KEY y SMTP/Firebase si se utilizan esas funciones.

Antes de migrar una base existente, realiza un respaldo nativo PostgreSQL y revisa los roles de las cuentas creadas por versiones anteriores. La migración inicial crea tablas faltantes y añade session_version; no borra datos ni intenta adivinar cómo corregir otras columnas antiguas. `prepare_db.py` aborta ante columnas faltantes para que se prepare una migración explícita. No hay downgrade destructivo automático.

## Respaldos

BACKUP_DIR necesita almacenamiento persistente. `python run_backups.py` es una tarea de una ejecución: prográmala desde un entorno con acceso a la misma base y almacenamiento. Activar una opción en la interfaz no instala un cron. Copiar respaldos cifrados a almacenamiento externo y comprobar recuperación periódicamente. No depender del disco efímero de Railway.

Los nuevos JSON contienen un sobre cifrado autenticado. Exportaciones de una clínica omiten hashes y no se pueden restaurar como si fueran toda la plataforma. Restauración global: agrega filas faltantes, actualiza las presentes y revoca sesiones; no elimina filas sobrantes. Los JSON antiguos sin cifrar necesitan revisión/conversión explícita y no se aceptan por el endpoint nuevo. El respaldo de aplicación no sustituye un dump nativo para recuperación exacta de PostgreSQL.

## Límites de esta entrega

Las políticas por rol, clínica y vínculo se validan en el servidor. La matriz editable sigue sin controlar de manera uniforme todos los módulos; no debe interpretarse como un sistema completo de permisos dinámicos. Las pruebas locales no acreditan servicios de pago, correo, IA ni PostgreSQL desplegado. CI añade comprobación de migraciones en PostgreSQL.


## Comprobantes de suscripción

La migración 20261012_02 añade subscription_receipts. Ejecutar `python prepare_db.py` antes de iniciar esta versión. Cada pago nuevo confirmado guarda un recibo con datos del comprador, importe, moneda, orden/captura PayPal, fechas y marca Sandbox; verificar repetidamente la misma orden no crea otro recibo. Cancelar, vencer o renovar no lo elimina. Eliminar la cuenta/suscripción elimina sus comprobantes por cascada, coherentemente con los demás datos de la cuenta.

GET /api/v1/subscriptions/receipts lista los recibos del usuario autenticado con limit/offset. GET /api/v1/subscriptions/receipts/{id}/pdf genera su PDF con Bearer token y verificación de propietario. No se aceptan tokens en la URL. Son recibos internos, no facturas fiscales. No se fabrican retrospectivamente comprobantes para activaciones antiguas que pudieron usar el flujo Sandbox sin cobro.
