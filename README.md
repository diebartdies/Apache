# Apache

Apache server + Music Store + Ansible + PostgreSQL disc sync.

## Run

Start everything:

- Apache + web.py app (port 8585)
- PostgreSQL (port 5432)

Use:

docker compose up --build

## URLs

- Store: [http://localhost:8585/](http://localhost:8585/)
- CGI store: [http://localhost:8585/cgi-bin/store.py](http://localhost:8585/cgi-bin/store.py)
- Sync discs to PostgreSQL: [http://localhost:8585/sync-discs](http://localhost:8585/sync-discs)

## PostgreSQL defaults

The stack now includes a PostgreSQL service with these defaults:

- Host: `postgres` (inside Docker network), `localhost` (from host machine)
- Port: `5432`
- Database: `musicstore`
- User: `musicstore`
- Password: `musicstore`

The app reads DB connection from either:

- `DATABASE_URL`, or
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`

## What gets synced

Disc data is discovered from `ALBUMS_DIR` album folders and synced into:

- `albums` table
- `tracks` table (`*.wav` files per album)

Sync runs in two ways:

1. On container startup (default enabled via `SYNC_DISCS_ON_STARTUP=1`)
2. On demand via `/sync-discs`
