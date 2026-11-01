# Consulta general y wallet

## Flujo

1. El cliente solicita la consulta general desde móvil. Queda `PENDING`.
2. El equipo acepta desde Gestión de citas (`POST /appointments/{id}/confirm`, ruta conservada por compatibilidad). Pasa a `ACCEPTED`, crea un único cobro pendiente de USD 5 con paciente, empresa y sucursal de la cita.
3. El botón «Cobrar USD 5» abre la caja web con los datos del paciente. Se registra efectivo o se inicia PayPal. En móvil, el cliente elige wallet o PayPal después de la aceptación.
4. El cobro completado y el cambio a `CONFIRMED` se guardan en una transacción. Se notifica al paciente y se conserva el comprobante. La web consulta cambios cada 12 segundos y el móvil cada 15 segundos mientras está en primer plano.

Las citas confirmadas de versiones anteriores conservan su estado y no reciben cobros retroactivos.

## Wallet en Perfil

- Recargas fijas: USD 5, 10, 20 o 50, exclusivamente por PayPal Sandbox.
- La solicitud incluye `Idempotency-Key`. La misma clave reanuda la operación original y rechaza un importe distinto.
- El saldo se obtiene del libro de movimientos decimales del paciente; no se modifica en el teléfono.
- Una recarga solo se acredita cuando PayPal devuelve captura `COMPLETED`, con importe y moneda coincidentes. Las verificaciones repetidas no vuelven a acreditar.
- Pagar una consulta debita USD 5 y confirma la cita en la misma transacción. Se bloquean saldo insuficiente, pacientes ajenos y cobros antes de aceptar.
- Al volver de PayPal, la app verifica automáticamente la orden activa (o la más reciente pendiente al abrir la pantalla). «Verificar pago» permite reintentar manualmente. Las órdenes pendientes y «Volver a abrir PayPal» permanecen disponibles desde Perfil aunque se cierre la pantalla.
- Las recargas son saldo anticipado: no se suman como ingresos por servicios en estadísticas ni reportes, para evitar contarlas otra vez cuando se paga la consulta.

No se cambia a efectivo/wallet mientras exista una orden PayPal iniciada, para evitar dos cobros por la misma consulta. La devolución de una cita ya pagada requiere un proceso administrativo; este cambio no implementa reembolsos ni cancelaciones de pagos capturados.

## Despliegue

Desplegar primero backend y después web/móvil. La migración `20261031_08` añade referencias a paciente/cita y `purpose` a pagos, y crea `wallet_movements`. `railway.toml` ya ejecuta `python prepare_db.py` como predeploy; si Railway tiene otra configuración manual, conservar ese paso. No ejecutar SQL para simular saldo.

Se usan las variables PayPal existentes (`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`) y `FRONTEND_URL` real para retornos de caja web. El retorno móvil es `/api/v1/payments/mobile-return`: invita a volver a la app; no acredita pagos con parámetros de URL.

Las pruebas usan respuestas PayPal simuladas. Antes de dar por operativo el despliegue, realizar una recarga con comprador Sandbox y pagar una cita aceptada; comprobar saldo, historial, comprobante y estado en web. Las pruebas locales no verifican las credenciales del proveedor en Railway.

Referencia del proveedor: https://developer.paypal.com/api/make-api-requests (idempotencia) y https://developer.paypal.com/api/rest/integration/orders-api (aprobación y captura).


## Modalidad y columna de pago en citas

Después de aceptar una cita, el cliente elige Online o Presencial. Elegir modalidad usa la misma ruta de pago con method=ONLINE/PRESENCIAL y solo actualiza el cobro pendiente; nunca marca el pago como completado. Online habilita wallet y PayPal. Presencial queda pendiente hasta el cobro en caja. Una orden PayPal iniciada debe verificarse antes de cambiar de modalidad.

La API de citas devuelve payment_status (PAID, UNPAID o UNRECORDED), payment_preference (ONLINE o PRESENCIAL), payment_method y payment_id. Estos datos se obtienen del cobro vinculado, no del estado de la cita. Una cita antigua confirmada sin cobro vinculado se muestra como «Sin pago registrado»; no se presume que esté pagada. Gestión de citas muestra la columna Pago de solo lectura y las acciones de aceptación/cancelación; caja conserva el registro del cobro.

Si /openapi.json no incluye /api/v1/wallet y /api/v1/wallet/topups, la versión publicada aún no contiene este módulo. En Railway, Redeploy repite el código de un despliegue anterior. Usar la paleta de comandos, Deploy Latest Commit, para desplegar main desde el repositorio backend correcto. Luego comprobar los Deploy Logs, el paso python prepare_db.py y las rutas publicadas. Referencia: https://docs.railway.com/deployments/deployment-actions.
