import html
import base64
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error

import web
from ldap3 import Connection, Server, Tls
from waitress import serve

from disc_sync import is_database_configured, sync_discs

urls = (
    "/", "Index",
    "/buy", "Buy",
    "/album", "Album",
    "/media", "Media",
    "/sync-discs", "SyncDiscs",
    "/login", "Login",
    "/auth/callback", "AuthCallback",
    "/logout", "Logout",
)

_MEDIA_ALLOWED_EXTENSIONS = {".wav", ".jpg", ".jpeg"}
_MEDIA_CHUNK_SIZE = 64 * 1024

_FALLBACK_PRODUCTS = [
    {"id": "album-001", "name": "Lo-Fi Nights (Album)", "price": 9.99},
    {"id": "ep-002", "name": "Synth Sunrise (EP)", "price": 4.99},
    {"id": "single-003", "name": "Acoustic Breeze (Single)", "price": 1.29},
]


def _load_products_from_dir(albums_dir: str):
    root = Path(albums_dir)
    if not root.exists() or not root.is_dir():
        return [], f"Configured ALBUMS_DIR not found: {albums_dir}"

    products = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            jpgs = sorted(
                [p for p in [*entry.glob("*.jpg"), *entry.glob("*.jpeg")] if "3000x3000" in p.name.lower()],
                key=lambda p: p.name.lower(),
            )
            thumb_url = None
            if jpgs:
                thumb_url = jpgs[0].name

            products.append(
                {
                    "id": f"dir-{entry.name}",
                    "name": entry.name,
                    "folder": entry.name,
                    "thumb": thumb_url,
                    "price": 9.99,
                }
            )

    if not products:
        return [], f"No album folders found in: {albums_dir}"

    return products, f"Loaded albums from: {albums_dir}"


def load_products():
    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    if not albums_dir:
        return _FALLBACK_PRODUCTS, None

    products, note = _load_products_from_dir(albums_dir)
    if products:
        return products, note
    return _FALLBACK_PRODUCTS, note


PRODUCTS, PRODUCTS_NOTE = load_products()
PRODUCT_BY_ID = {p["id"]: p for p in PRODUCTS}


def _get_media_signing_key() -> str:
    return os.environ.get("MEDIA_SIGNING_KEY", "dev-only-change-me").strip() or "dev-only-change-me"


def _get_media_url_ttl_seconds() -> int:
    raw = os.environ.get("MEDIA_URL_TTL_SECONDS", "300").strip()
    try:
        ttl = int(raw)
    except ValueError:
        return 300
    return max(30, min(ttl, 3600))


def _media_signature(album: str, file_name: str, exp: int) -> str:
    payload = f"{album}\n{file_name}\n{exp}".encode("utf-8")
    return hmac.new(_get_media_signing_key().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _build_media_url(album: str, file_name: str) -> str:
    exp = int(time.time()) + _get_media_url_ttl_seconds()
    sig = _media_signature(album, file_name, exp)
    qs = urllib.parse.urlencode({"album": album, "file": file_name, "exp": str(exp), "sig": sig})
    return f"/media?{qs}"


def _resolve_media_path(album: str, file_name: str):
    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    if not albums_dir:
        return None, "Album directory not configured."

    root_path = Path(albums_dir).resolve()
    media_path = (root_path / album / file_name).resolve()
    if root_path not in media_path.parents:
        return None, "Invalid media path."

    if media_path.suffix.lower() not in _MEDIA_ALLOWED_EXTENSIONS:
        return None, "Unsupported media type."

    return media_path, None


def _iter_file_bytes(path: Path, start: int, end: int):
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(_MEDIA_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _oidc_enabled() -> bool:
    return bool(
        os.environ.get("OIDC_CLIENT_ID", "").strip()
        and os.environ.get("OIDC_CLIENT_SECRET", "").strip()
        and os.environ.get("OIDC_REDIRECT_URI", "").strip()
        and (
            os.environ.get("OIDC_AUTHORITY", "").strip()
            or os.environ.get("OIDC_TENANT_ID", "").strip()
        )
    )


def _oidc_authority() -> str:
    authority = os.environ.get("OIDC_AUTHORITY", "").strip().rstrip("/")
    if authority:
        return authority

    tenant_id = os.environ.get("OIDC_TENANT_ID", "common").strip() or "common"
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0"


def _oidc_endpoints() -> dict[str, str]:
    authority = _oidc_authority()
    return {
        "authorize": f"{authority}/oauth2/v2.0/authorize",
        "token": f"{authority}/oauth2/v2.0/token",
        "logout": f"{authority}/oauth2/v2.0/logout",
        "userinfo": os.environ.get("OIDC_USERINFO_ENDPOINT", "https://graph.microsoft.com/oidc/userinfo").strip(),
    }


def _oidc_scopes() -> str:
    return os.environ.get("OIDC_SCOPES", "openid profile email").strip() or "openid profile email"


def _ldap_enabled() -> bool:
    return bool(
        os.environ.get("LDAP_SERVER_URL", "").strip()
        and os.environ.get("LDAP_BASE_DN", "").strip()
    )


def _auth_enabled() -> bool:
    return _ldap_enabled() or _oidc_enabled()


def _base_dn_to_domain(base_dn: str) -> str:
    parts = []
    for piece in base_dn.split(","):
        clean = piece.strip()
        if clean.lower().startswith("dc="):
            parts.append(clean.split("=", 1)[1])
    return ".".join(parts)


def _ldap_bind_user(username: str) -> str:
    candidate = username.strip()
    if "@" in candidate:
        return candidate

    upn_domain = os.environ.get("LDAP_UPN_DOMAIN", "").strip()
    if not upn_domain:
        upn_domain = _base_dn_to_domain(os.environ.get("LDAP_BASE_DN", "").strip())

    if upn_domain:
        return f"{candidate}@{upn_domain}"
    return candidate


def _ldap_validate_certs() -> bool:
    raw = os.environ.get("LDAP_VALIDATE_CERTS", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _ldap_authenticate(username: str, password: str) -> tuple[bool, str, dict]:
    server_url = os.environ.get("LDAP_SERVER_URL", "").strip()
    if not server_url:
        return False, "LDAP server is not configured.", {}

    if not username or not password:
        return False, "Username and password are required.", {}

    use_ssl = server_url.lower().startswith("ldaps://")
    ca_file = os.environ.get("LDAP_CA_CERT_FILE", "").strip()
    tls = None
    if use_ssl:
        tls = Tls(
            validate=ssl.CERT_REQUIRED if _ldap_validate_certs() else ssl.CERT_NONE,
            ca_certs_file=ca_file or None,
        )

    try:
        server = Server(server_url, use_ssl=use_ssl, tls=tls, connect_timeout=8)
        bind_user = _ldap_bind_user(username)
        with Connection(server, user=bind_user, password=password, auto_bind=True, receive_timeout=8):
            return True, "", {
                "name": username,
                "preferred_username": bind_user,
                "email": bind_user if "@" in bind_user else "",
                "sub": bind_user,
            }
    except Exception:
        return False, "Invalid LDAP credentials or LDAP server unavailable.", {}


def _is_authenticated() -> bool:
    return bool(session.get("user"))


def _current_user_display_name() -> str:
    user = session.get("user")
    if not user:
        return ""
    return (
        user.get("name")
        or user.get("preferred_username")
        or user.get("email")
        or "User"
    )


def _decode_jwt_payload_unverified(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _http_post_form(url: str, form_data: dict[str, str]) -> dict:
    encoded = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _require_authentication(next_path: str = "/"):
    if not _auth_enabled():
        return
    if _is_authenticated():
        return
    session["post_login_redirect"] = next_path
    raise web.seeother("/login")


def find_product(product_id: str):
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    return None


def _is_mobile_request() -> bool:
    user_agent = (web.ctx.env.get("HTTP_USER_AGENT", "") or "").lower()
    mobile_tokens = (
        "android",
        "iphone",
        "ipad",
        "ipod",
        "mobile",
        "windows phone",
        "opera mini",
    )
    return any(token in user_agent for token in mobile_tokens)


def page(title: str, body: str) -> str:
    is_mobile = _is_mobile_request()
    device_class = "mobile" if is_mobile else "desktop"
    sync_label = "Sync discs" if is_mobile else "Sync discs to PostgreSQL"
    signed_in = _is_authenticated()
    whoami = web.websafe(_current_user_display_name()) if signed_in else ""
    auth_nav = ""
    if _auth_enabled():
        if signed_in:
            auth_nav = f'<span style="margin-right:12px; color: var(--soft-ink);">{whoami}</span><a href="/logout">Logout</a>'
        else:
            auth_nav = '<a href="/login">Login</a>'

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <style>
      :root {{
        --ink: #0f2b46;
        --soft-ink: #35526b;
        --card: rgba(255, 255, 255, 0.86);
        --line: rgba(10, 39, 74, 0.12);
        --accent: #2a82d8;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        color: var(--ink);
        font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
        background-image: url('/Webback.jpg');
        background-repeat: repeat;
        background-attachment: fixed;
        background-size: 80px 80px;
      }}
      .page {{
        max-width: 1060px;
        margin: 26px auto;
        padding: 0 16px;
      }}
      .hero {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 14px;
        flex-wrap: wrap;
        margin-bottom: 24px;
        padding: 18px 20px;
        border: none;
        border-radius: 16px;
        background: var(--card);
        backdrop-filter: blur(2px);
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
      }}
      .brand {{
        display: flex;
        align-items: center;
        gap: 12px;
      }}
      .logo {{
        width: 52px;
        height: 52px;
        border-radius: 12px;
        object-fit: cover;
        box-shadow: 0 6px 16px rgba(42, 130, 216, .35);
        flex-shrink: 0;
      }}
      h1 {{
        margin: 0;
        font-size: clamp(1.3rem, 2vw, 1.85rem);
        line-height: 1.25;
      }}
      h2 {{ margin: 20px 0 18px; font-size: 1.4rem; font-weight: 800; }}
      .nav a {{
        color: var(--ink);
        text-decoration: none;
        margin-right: 14px;
        font-weight: 600;
      }}
      .nav a:hover {{ color: var(--accent); }}
      .content {{
        border: none;
        border-radius: 16px;
        padding: 24px;
        background: rgba(222, 184, 135, 0.95);
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
      }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr));
                gap: clamp(12px, 2vw, 20px);
                padding: 0;
            }}
      .card {{ border: none; border-radius: 16px; padding: 16px; background: rgba(100, 150, 220, 0.9); box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15); transition: all 0.3s ease; cursor: pointer; display: flex; flex-direction: column; height: 100%; min-height: 380px; }}
      .card:hover {{ transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25); }}
      .thumb {{ width: 100%; max-width: 300px; height: auto; display: block; border-radius: 12px; margin-bottom: 10px; transition: transform 0.3s ease; }}
      .card:hover .thumb {{ transform: scale(1.05); }}
      .title {{ margin: 12px 0 8px 0; font-weight: 800; font-size: clamp(0.9rem, 4vw, 1.1rem); color: #000000; text-transform: uppercase; word-wrap: break-word; overflow-wrap: break-word; }}
      .meta {{ margin: 0 0 12px 0; color: #1a1a1a; font-size: 1.2rem; font-weight: 700; }}
      .actions {{ display: flex; gap: 8px; margin-top: auto; }}
      .actions a {{ flex: 1; padding: 10px 12px; background: #000000; color: #ffffff; text-decoration: none; font-weight: 700; border-radius: 8px; text-align: center; transition: all 0.2s ease; font-size: 0.95rem; }}
      .actions a:hover {{ background: #333333; transform: scale(1.05); box-shadow: 0 4px 8px rgba(0, 0, 0, 0.3); }}
      .actions a:hover {{ text-decoration: underline; }}
      ul {{ padding-left: 20px; }}
      li {{ margin-bottom: 14px; }}
      audio {{ width: min(520px, 100%); margin-top: 6px; }}
      em {{ color: var(--soft-ink); }}
            body.mobile .page {{ margin: 14px auto; padding: 0 10px; }}
            body.mobile .hero {{ padding: 14px; border-radius: 14px; }}
            body.mobile .content {{ padding: 14px; }}
            body.mobile .card {{ min-height: 0; padding: 12px; }}
            body.mobile .actions {{ flex-direction: column; }}
            body.mobile .actions a {{ width: 100%; }}
            body.mobile .nav a {{ margin-right: 10px; font-size: 0.9rem; }}
    </style>
  </head>
    <body class="{device_class}">
    <main class="page">
      <header class="hero">
        <div class="brand">
          <img class="logo" src="/ormet.jpeg" alt="Music Store Logo" />
          <div>
            <h1>{html.escape(title)}</h1>
            <p style="margin: 4px 0 0 0; color: var(--soft-ink); font-size: 0.95rem; font-weight: 500;">Ormet</p>
          </div>
        </div>
        <nav class="nav" aria-label="Main">
          <a href="/">Store</a>
                    <a href="/sync-discs">{sync_label}</a>
                    {auth_nav}
        </nav>
      </header>
      <section class="content">{body}</section>
    </main>
  </body>
</html>
"""


class Index:
    def GET(self):
        web.header('Referrer-Policy', 'origin-when-cross-origin')
        cards = []
        for product in PRODUCTS:
            pid = web.websafe(product["id"])
            name = web.websafe(product["name"])
            price = web.websafe(f"${product['price']:.2f}")

            thumb = ""
            if product.get("thumb") and product.get("folder"):
                src = web.websafe(_build_media_url(product["folder"], product["thumb"]))
                alt = name
                thumb = f"<img class=\"thumb\" src=\"{src}\" alt=\"{alt}\" loading=\"lazy\" />"

            cards.append(
                "<div class=\"card\">"
                f"{thumb}"
                f"<div class=\"title\">{name}</div>"
                f"<div class=\"meta\">{price}</div>"
                "<div class=\"actions\">"
                f"<a href=\"/album?id={pid}\">Details</a>"
                f"<a href=\"/buy?id={pid}\">Buy</a>"
                "</div>"
                "</div>"
            )

        note = "" if not PRODUCTS_NOTE else f"<p><em>{web.websafe(PRODUCTS_NOTE)}</em></p>"
        body = note + "<h2>Albums</h2><div class=\"grid\">" + "".join(cards) + "</div>"
        return page("Music Store", body)


class Album:
    def GET(self):
        user_input_id = web.input(id="").id
        product = PRODUCT_BY_ID.get(user_input_id)
        if not product:
            return page("Album", "<p>Unknown album.</p>")

        albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
        if not albums_dir:
            return page("Album", "<p>Album directory not configured (ALBUMS_DIR is empty).</p>")

        album_folder = product.get("folder") or product["name"]
        album_path = (Path(albums_dir) / album_folder).resolve()
        root_path = Path(albums_dir).resolve()
        if root_path not in album_path.parents and album_path != root_path:
            return page("Album", "<p>Invalid album path.</p>")

        if not album_path.exists() or not album_path.is_dir():
            return page("Album", "<p>Album folder not found.</p>")

        wavs = sorted(album_path.glob("*.wav"), key=lambda p: p.name.lower())
        if not wavs:
            return page("Album", "<p>No .wav files found for this album.</p>")

        cover = ""
        jpgs = sorted(
            [p for p in [*album_path.glob("*.jpg"), *album_path.glob("*.jpeg")] if "3000x3000" in p.name.lower()],
            key=lambda p: p.name.lower(),
        )
        if jpgs:
            src = _build_media_url(album_folder, jpgs[0].name)
            cover = f"<p><img src=\"{src}\" alt=\"{web.websafe(product['name'])}\" width=\"240\" loading=\"lazy\" /></p>"

        rows = []
        for wav in wavs:
            wav_name = wav.name
            safe_label = web.websafe(wav_name)
            src = _build_media_url(album_folder, wav_name)
            rows.append(
                "<li>"
                f"<div>{safe_label}</div>"
                f"<audio controls preload=\"none\" src=\"{src}\"></audio>"
                "</li>"
            )

        body = f"<h2>{web.websafe(product['name'])}</h2>" + cover + "<ul>" + "".join(rows) + "</ul>"
        return page("Album", body)


class Buy:
    def GET(self):
        _require_authentication(next_path=web.ctx.fullpath)
        user_input_id = web.input(id="").id
        product = find_product(user_input_id)
        if not product:
            return page("Purchase", "<p>Unknown product.</p>")

        safe_name = web.websafe(product["name"])
        safe_price = web.websafe(f"${product['price']:.2f}")
        body = (
            f"<p>Order received for <strong>{safe_name}</strong> ({safe_price}).</p>"
            "<p>This is a demo checkout (no payment processing).</p>"
        )
        return page("Purchase", body)


class Media:
    def GET(self):
        req = web.input(album="", file="", exp="", sig="")
        album = (req.album or "").strip()
        file_name = (req.file or "").strip()
        exp_raw = (req.exp or "").strip()
        sig = (req.sig or "").strip()

        if not album or not file_name or not exp_raw or not sig:
            web.ctx.status = "400 Bad Request"
            return b"Missing media signature parameters."

        try:
            exp = int(exp_raw)
        except ValueError:
            web.ctx.status = "400 Bad Request"
            return b"Invalid expiry."

        if int(time.time()) > exp:
            web.ctx.status = "403 Forbidden"
            return b"Media URL expired."

        expected_sig = _media_signature(album, file_name, exp)
        if not hmac.compare_digest(sig, expected_sig):
            web.ctx.status = "403 Forbidden"
            return b"Invalid media signature."

        media_path, path_error = _resolve_media_path(album, file_name)
        if path_error:
            web.ctx.status = "403 Forbidden"
            return path_error.encode("utf-8")

        if not media_path or not media_path.exists() or not media_path.is_file():
            web.ctx.status = "404 Not Found"
            return b"Media file not found."

        total_size = media_path.stat().st_size
        if total_size <= 0:
            web.ctx.status = "404 Not Found"
            return b"Media file is empty."

        content_type, _ = mimetypes.guess_type(media_path.name)
        if not content_type:
            content_type = "application/octet-stream"

        start = 0
        end = total_size - 1
        status = "200 OK"
        range_header = web.ctx.env.get("HTTP_RANGE", "")
        if range_header.startswith("bytes="):
            raw_range = range_header[len("bytes="):].split(",", 1)[0].strip()
            if "-" in raw_range:
                left, right = raw_range.split("-", 1)
                try:
                    if left == "":
                        suffix_len = int(right)
                        if suffix_len > 0:
                            start = max(total_size - suffix_len, 0)
                    else:
                        start = int(left)
                    if right != "":
                        end = int(right)
                except ValueError:
                    start = 0
                    end = total_size - 1

                start = max(0, min(start, total_size - 1))
                end = max(start, min(end, total_size - 1))
                status = "206 Partial Content"

        web.ctx.status = status
        web.header("Content-Type", content_type)
        web.header("Accept-Ranges", "bytes")
        web.header("Cache-Control", "private, max-age=60")
        web.header("X-Content-Type-Options", "nosniff")

        content_length = end - start + 1
        web.header("Content-Length", str(content_length))
        if status == "206 Partial Content":
            web.header("Content-Range", f"bytes {start}-{end}/{total_size}")

        return _iter_file_bytes(media_path, start, end)


class SyncDiscs:
    def GET(self):
        _require_authentication(next_path=web.ctx.fullpath)
        if not is_database_configured():
            return page(
                "Sync discs",
                "<p>PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* environment variables.</p>",
            )

        albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
        try:
            result = sync_discs(PRODUCTS, albums_dir)
        except Exception as exc:
            return page("Sync discs", f"<p>Sync failed: {web.websafe(str(exc))}</p>")

        body = (
            "<p>Sync completed successfully.</p>"
            f"<p>Albums synced: <strong>{result['albums_synced']}</strong></p>"
            f"<p>Tracks synced: <strong>{result['tracks_synced']}</strong></p>"
        )
        return page("Sync discs", body)


class Login:
    def GET(self):
        if not _auth_enabled():
            return page("Login", "<p>Authentication is not configured. Set LDAP_* or OIDC_* environment variables.</p>")

        if _is_authenticated():
            raise web.seeother("/")

        if _ldap_enabled():
            req = web.input(next="")
            next_path = (req.next or "").strip()
            if next_path.startswith("/"):
                session["post_login_redirect"] = next_path

            body = (
                "<h2>Login</h2>"
                "<form method=\"post\" action=\"/login\" style=\"max-width:420px;\">"
                "<p><label>Username<br /><input name=\"username\" autocomplete=\"username\" required style=\"width:100%;padding:10px;border-radius:8px;border:1px solid #b5b5b5;\" /></label></p>"
                "<p><label>Password<br /><input type=\"password\" name=\"password\" autocomplete=\"current-password\" required style=\"width:100%;padding:10px;border-radius:8px;border:1px solid #b5b5b5;\" /></label></p>"
                "<p><button type=\"submit\" style=\"padding:10px 14px;border-radius:8px;border:none;background:#000;color:#fff;font-weight:700;\">Sign in</button></p>"
                "</form>"
            )
            return page("Login", body)

        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        session["oidc_state"] = state
        session["oidc_nonce"] = nonce

        req = web.input(next="")
        next_path = (req.next or "").strip()
        if next_path.startswith("/"):
            session["post_login_redirect"] = next_path

        endpoints = _oidc_endpoints()
        params = {
            "client_id": os.environ.get("OIDC_CLIENT_ID", "").strip(),
            "response_type": "code",
            "redirect_uri": os.environ.get("OIDC_REDIRECT_URI", "").strip(),
            "response_mode": "query",
            "scope": _oidc_scopes(),
            "state": state,
            "nonce": nonce,
        }
        raise web.seeother(f"{endpoints['authorize']}?{urllib.parse.urlencode(params)}")

    def POST(self):
        if not _ldap_enabled():
            raise web.seeother("/login")

        req = web.input(username="", password="")
        username = (req.username or "").strip()
        password = req.password or ""
        ok, message, user = _ldap_authenticate(username, password)
        if not ok:
            body = (
                "<h2>Login</h2>"
                f"<p style=\"color:#8b0000;\">{web.websafe(message)}</p>"
                "<form method=\"post\" action=\"/login\" style=\"max-width:420px;\">"
                f"<p><label>Username<br /><input name=\"username\" value=\"{web.websafe(username)}\" autocomplete=\"username\" required style=\"width:100%;padding:10px;border-radius:8px;border:1px solid #b5b5b5;\" /></label></p>"
                "<p><label>Password<br /><input type=\"password\" name=\"password\" autocomplete=\"current-password\" required style=\"width:100%;padding:10px;border-radius:8px;border:1px solid #b5b5b5;\" /></label></p>"
                "<p><button type=\"submit\" style=\"padding:10px 14px;border-radius:8px;border:none;background:#000;color:#fff;font-weight:700;\">Sign in</button></p>"
                "</form>"
            )
            return page("Login", body)

        session["user"] = user
        redirect_target = session.pop("post_login_redirect", "/")
        if not isinstance(redirect_target, str) or not redirect_target.startswith("/"):
            redirect_target = "/"
        raise web.seeother(redirect_target)


class AuthCallback:
    def GET(self):
        if not _oidc_enabled():
            return page("Login", "<p>SSO is not configured.</p>")

        req = web.input(code="", state="", error="", error_description="")
        if req.error:
            return page(
                "Login",
                f"<p>Login failed: {web.websafe(req.error)} {web.websafe(req.error_description or '')}</p>",
            )

        expected_state = session.get("oidc_state")
        if not expected_state or req.state != expected_state:
            return page("Login", "<p>Invalid login state. Please try again.</p>")

        code = (req.code or "").strip()
        if not code:
            return page("Login", "<p>Authorization code missing.</p>")

        endpoints = _oidc_endpoints()
        token_payload = {
            "client_id": os.environ.get("OIDC_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("OIDC_CLIENT_SECRET", "").strip(),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": os.environ.get("OIDC_REDIRECT_URI", "").strip(),
            "scope": _oidc_scopes(),
        }

        try:
            token_response = _http_post_form(endpoints["token"], token_payload)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = str(exc)
            return page("Login", f"<p>Token exchange failed: {web.websafe(detail)}</p>")
        except Exception as exc:
            return page("Login", f"<p>Token exchange failed: {web.websafe(str(exc))}</p>")

        id_token = token_response.get("id_token", "")
        access_token = token_response.get("access_token", "")

        claims = _decode_jwt_payload_unverified(id_token) if id_token else {}
        if access_token and endpoints.get("userinfo"):
            try:
                userinfo = _http_get_json(
                    endpoints["userinfo"],
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if isinstance(userinfo, dict):
                    claims.update(userinfo)
            except Exception:
                pass

        session["user"] = {
            "sub": claims.get("sub", ""),
            "name": claims.get("name") or claims.get("given_name") or "",
            "preferred_username": claims.get("preferred_username", ""),
            "email": claims.get("email", ""),
        }
        session.pop("oidc_state", None)
        session.pop("oidc_nonce", None)

        redirect_target = session.pop("post_login_redirect", "/")
        if not isinstance(redirect_target, str) or not redirect_target.startswith("/"):
            redirect_target = "/"
        raise web.seeother(redirect_target)


class Logout:
    def GET(self):
        session.pop("user", None)
        session.pop("oidc_state", None)
        session.pop("oidc_nonce", None)
        session.pop("post_login_redirect", None)
        raise web.seeother("/")


app = web.application(urls, globals())
session = web.session.Session(
    app,
    web.session.DiskStore("/tmp/webpy-sessions"),
    initializer={"user": None, "oidc_state": None, "oidc_nonce": None, "post_login_redirect": "/"},
)

if __name__ == "__main__":
    serve(app.wsgifunc(), host="127.0.0.1", port=9000)
