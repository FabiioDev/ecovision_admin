# Alta disponibilidad de datos: escritura doble en dos bases de Firestore

## Qué hace

El backend escribe cada operación en **dos bases de Firestore** del mismo proyecto:

| Variable | Valor en producción | Uso |
|---|---|---|
| `FIRESTORE_DATABASE` | `(default)` (us-central1) | Base principal |
| `FIRESTORE_REPLICA_DATABASE` | `ecovision-replica-2` (us-east1) | Segunda base. Vacía = una sola base, como antes |
| `FIRESTORE_RETRY_S` | `15` | Segundos que una base caída queda fuera antes de volver a probarla |

- **Escritura:** va a las dos bases. Basta con que una la acepte. Lo que no entró en la otra queda en la colección `_replication_pending` de la base viva y se reaplica cuando la otra responde.
- **Lectura:** de la principal; si falla, de la réplica.
- **Base borrada y vuelta a crear:** nace vacía. El backend lo detecta (le falta el documento `_replication_meta/marker`), la copia completa desde la otra base y no la usa para leer hasta terminar.
- **Transacción de la config:** el número de versión se decide en una sola base y los mismos documentos se copian a la otra.

El código está en `backend/app/services/replication.py`. Los servicios `admins`, `users`, `audit` y `config_store` escriben a través de él.

## Estado en vivo

`GET /api/v1/health/databases` devuelve el estado de cada base (`activa`, `caida`, `sincronizando`), las escrituras aceptadas y fallidas de la instancia que responde y las escrituras pendientes. No expone datos. Responde 503 solo si no queda ninguna base.

## Límites (decirlos tal cual)

- Es replicación hecha por la aplicación, no nativa de Firestore. Entre las dos bases no hay una transacción común.
- Si una base **no responde** (red caída, no borrada), el cliente de Firestore reintenta hasta 60 s antes de rendirse: esa petición tarda, las siguientes ya van directo a la otra base. Una base **borrada** sigue respondiendo de 14 a 60 s (medido en GCP el 2026-11-25); después responde `NotFound` al instante y el cambio a la otra base es inmediato.
- Si caen las dos bases, la app falla como antes.
- Firebase Auth no depende de estas bases.

## Recuperar la réplica después de borrarla

Google no permite reutilizar de inmediato el nombre de una base borrada. Se crea otra (`ecovision-replica-3`, ...), se cambia `FIRESTORE_REPLICA_DATABASE` en Cloud Run y se copia con el comando de abajo. El backend también la copia solo, pero en Cloud Run esa copia avanza solo mientras haya tráfico.

## Copia inicial y reparación manual

```bash
python scripts/resync_firestore.py --project ecovision-ai-model --source "(default)" --target ecovision-replica-2          # cuenta
python scripts/resync_firestore.py --project ecovision-ai-model --source "(default)" --target ecovision-replica-2 --apply  # copia
```

## Pruebas

```bash
docker run -d --name fs-emu -p 8085:8085 gcr.io/google.com/cloudsdktool/google-cloud-cli:585.0.0-emulators \
  gcloud emulators firestore start --host-port=0.0.0.0:8085
FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 python -m pytest
```

Sin el emulador, `tests/test_replication.py` se omite y el resto de la suite corre igual.
