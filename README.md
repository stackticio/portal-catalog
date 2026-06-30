# portal-catalog

Django + DRF catalog microservice for the Stacktic portal (eBay-like marketplace).

## Endpoints
- `GET  /health` — probe target (checks DB), used by `probe_path=/health`
- `GET  /api/catalog/listings/` — list products
- `POST /api/catalog/listings/` — create a listing
- `GET/PUT/DELETE /api/catalog/listings/{id}/`

## Environment (auto-injected by Stacktic links, framework_type=django)
| Var | Source link |
|-----|-------------|
| `DATABASE_URL` | `external_source_code-db` → cnpg `catalog_db` |
| `REDIS_URL` | `external_source_code-valkey` |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` | `external_source_code-swbucket` → `catalog-images` |

With no env set, it runs locally on sqlite + locmem (no infra needed).

## Run locally
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed
python manage.py runserver 0.0.0.0:8000
```

## Container
Builds via Kaniko in-cluster. Listens on `:8000`, runs as non-root UID/GID `10001`
(matches `user_id`/`group_id` when `pod_security` is enabled). `entrypoint.sh` runs
`migrate` then gunicorn.
