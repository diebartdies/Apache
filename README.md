# Apache

Apache server + Music Store + Ansible + PostgreSQL disc sync.

## Run

Start everything:

- Apache + web.py app (port 8585)
- PostgreSQL (port 5432)
- OpenLDAP/LDAPS (ports 389/636)

Use:

docker compose up --build

## URLs

- Store: [http://localhost:8585/](http://localhost:8585/)
- CGI store: [http://localhost:8585/cgi-bin/store.py](http://localhost:8585/cgi-bin/store.py)
- Sync discs to PostgreSQL: [http://localhost:8585/sync-discs](http://localhost:8585/sync-discs)

If OIDC is configured:

- Login: [http://localhost:8585/login](http://localhost:8585/login)
- Callback: [http://localhost:8585/auth/callback](http://localhost:8585/auth/callback)

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

## Optional SSO (Microsoft Entra ID via OIDC)

Set these environment variables to enable login:

- `OIDC_TENANT_ID` (or `OIDC_AUTHORITY`)
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`
- `OIDC_REDIRECT_URI`
- Optional: `OIDC_SCOPES` (default: `openid profile email`)
- Optional: `OIDC_USERINFO_ENDPOINT` (default: `https://graph.microsoft.com/oidc/userinfo`)

When OIDC is enabled, `/buy` and `/sync-discs` require login.

## Optional LDAP login (direct bind)

The web app can authenticate users directly against LDAP/LDAPS.

Required variables:

- `LDAP_SERVER_URL` (example: `ldaps://192.168.1.8:636`)
- `LDAP_BASE_DN` (example: `DC=drsrv,DC=net,DC=ar`)

Optional variables:

- `LDAP_UPN_DOMAIN` (example: `drsrv.net.ar`)
- `LDAP_VALIDATE_CERTS` (`0` or `1`)
- `LDAP_CA_CERT_FILE` (CA file path inside container)

When LDAP (or OIDC) is configured, `/buy` and `/sync-discs` require login.

## What gets synced

Disc data is discovered from `ALBUMS_DIR` album folders and synced into:

- `albums` table
- `tracks` table (`*.wav` files per album)

Sync runs in two ways:

1. On container startup (default enabled via `SYNC_DISCS_ON_STARTUP=1`)
2. On demand via `/sync-discs`

## Optional Alpine OpenLDAP service

This repo now includes an Alpine-based OpenLDAP server in [ldap/Dockerfile](ldap/Dockerfile).

Default LAN-exposed ports:

- LDAP: `389`
- LDAPS: `636`

Relevant environment variables:

- `LDAP_PORT`
- `LDAPS_PORT`
- `LDAP_ORGANISATION`
- `LDAP_DOMAIN`
- `LDAP_BASE_DN`
- `LDAP_ADMIN_PASSWORD`
- `LDAP_TLS_CN`
- `LDAP_DEFAULT_USER`
- `LDAP_DEFAULT_PASSWORD`

Bring it up with:

- `docker compose up --build -d ldap`

Test from another machine on the local network:

- `Test-NetConnection <docker-host-ip> -Port 389`
- `Test-NetConnection <docker-host-ip> -Port 636`

If LDAPS uses a self-signed certificate, clients must trust that certificate or CA.
