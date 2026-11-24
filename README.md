# PLC counter → API → Flutter

## PLC prerequisites (TIA Portal V13, done manually)
- CPU properties → Protection → "Permit access with PUT/GET communication from remote partner" = ON.
- Any DB added to `TAGS` later: DB properties → Attributes → "Optimized block access" = OFF, then download.
- The collector is read-only (no snap7 write/stop calls; enforced by `ReadOnlyClient`).

## Config
`cp .env.example .env` (PowerShell: `Copy-Item .env.example .env`), then set passwords, keys, `DOMAIN`, `API_URL`.

## VPS: database + API (+ HTTPS)
```
docker compose --profile https up -d --build db api caddy
curl https://$DOMAIN/health
```
Migrations run automatically on API start (`alembic upgrade head`).

## Factory LAN machine: collector
```
docker compose --profile collector up -d --build collector
docker compose logs -f collector
```
Simulated PLC: set `PLC_READER=fake` in `.env`. Buffer survives restarts (volume `collector_buffer`).

## Tests (Windows PowerShell)
```
cd api;       py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt; .venv\Scripts\pytest
cd ..\collector; py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt; .venv\Scripts\pytest
```
Against Postgres: `$env:TEST_DATABASE_URL="postgresql+psycopg://user:pw@localhost:5432/plc_test"` (empty DB).

## API
All endpoints except `/health` need header `X-API-Key` (`APP_API_KEY` to read, `INGEST_API_KEY` for `POST /ingest`).
`GET /health` · `GET /readings/latest` · `GET /readings?from&to&limit&offset` · `GET /stats?from&to&bucket=hour|day|shift`
Timestamps are UTC ISO-8601; buckets are aligned to Africa/Tunis time. Docs: `/docs`.
