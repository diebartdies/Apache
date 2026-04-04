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
- Spotify dashboard: [http://localhost:8585/spotify](http://localhost:8585/spotify)
- CGI store: [http://localhost:8585/cgi-bin/store.py](http://localhost:8585/cgi-bin/store.py)
- Sync discs to PostgreSQL: [http://localhost:8585/sync-discs](http://localhost:8585/sync-discs)

## Spotify data viewer

The web app can render a dashboard from your Spotify account export JSON files.

1. Set `SPOTIFY_HOST_PATH` in `.env` to your export folder on host.
2. Start/rebuild: `docker compose up --build -d`
3. Open `/spotify`.

Example `.env` value on Windows:

- `SPOTIFY_HOST_PATH="D:/spotify/my_spotify_data (2)/Spotify Account Data"`

If OIDC is configured:

- Login: [http://localhost:8585/login](http://localhost:8585/login)
- Callback: [http://localhost:8585/auth/callback](http://localhost:8585/auth/callback)

## Payments (Stripe + Mercado Pago)

The store can now send buyers to hosted checkout pages.

Supported providers:

- Stripe
- Mercado Pago

Recommended setup:

- Stripe for global buyers
- Mercado Pago for Latin America / local payment methods

Environment variables:

- `PUBLIC_BASE_URL` (recommended when accessed through a public host)
- `STRIPE_SECRET_KEY`
- `STRIPE_CURRENCY` (default: `usd`)
- `MERCADOPAGO_ACCESS_TOKEN`
- `MERCADOPAGO_CURRENCY` (default: `USD`)

Flow:

1. User opens `/buy?id=<product-id>`
2. User chooses Stripe or Mercado Pago
3. App redirects to hosted checkout
4. Provider returns to `/checkout/success` or `/checkout/cancel`
5. App verifies the payment and shows secure download links

Notes:

- Download links are time-limited using the existing media signing logic.
- If no provider keys are configured, the payment screen explains what is missing.

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
