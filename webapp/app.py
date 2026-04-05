import html
import base64
from datetime import date
from difflib import SequenceMatcher
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error
import unicodedata

import web
from ldap3 import Connection, Server, Tls
from waitress import serve

from disc_sync import (
    is_database_configured,
    sync_discs,
    save_subscriber,
    scan_products_from_dir,
    fetch_products_from_db,
    fetch_tracks_for_album,
)
from payments import configured_providers, create_checkout, verify_checkout
from spotify_dashboard import build_spotify_dashboard_body

urls = (
    "/", "Index",
    "/spotify", "Spotify",
    "/buy", "Buy",
    "/checkout/success", "CheckoutSuccess",
    "/checkout/cancel", "CheckoutCancel",
    "/album", "Album",
    "/media", "Media",
    "/sync-discs", "SyncDiscs",
    "/register", "Register",
    "/login", "Login",
    "/auth/callback", "AuthCallback",
    "/logout", "Logout",
)

_MEDIA_ALLOWED_EXTENSIONS = {".wav", ".jpg", ".jpeg", ".png", ".webp"}
_MEDIA_CHUNK_SIZE = 64 * 1024
_FX_CACHE_DATE: date | None = None
_FX_CACHE_RATE: float | None = None

_FALLBACK_PRODUCTS = [
    {"id": "album-001", "name": "Lo-Fi Nights (Album)", "price": 9.99},
    {"id": "ep-002", "name": "Synth Sunrise (EP)", "price": 4.99},
    {"id": "single-003", "name": "Acoustic Breeze (Single)", "price": 1.29},
]


def _load_products_from_dir(albums_dir: str):
    return scan_products_from_dir(albums_dir)


def _load_products_from_db():
    try:
        products = fetch_products_from_db()
    except Exception as exc:
        return [], f"Could not load catalog from PostgreSQL: {exc}"

    if not products:
        return [], "PostgreSQL catalog is empty."

    return products, f"Loaded {len(products)} albums from PostgreSQL catalog."


def load_products():
    if is_database_configured():
        products, note = _load_products_from_db()
        if products:
            return products, note

    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    if not albums_dir:
        return _FALLBACK_PRODUCTS, None

    products, note = _load_products_from_dir(albums_dir)
    if products:
        return products, note
    return _FALLBACK_PRODUCTS, note


PRODUCTS, PRODUCTS_NOTE = load_products()
PRODUCT_BY_ID = {p["id"]: p for p in PRODUCTS}


def _refresh_catalog() -> None:
    global PRODUCTS, PRODUCTS_NOTE, PRODUCT_BY_ID
    PRODUCTS, PRODUCTS_NOTE = load_products()
    PRODUCT_BY_ID = {p["id"]: p for p in PRODUCTS}


def _product_display_name(product: dict) -> str:
    band = (product.get("band") or "").strip()
    name = (product.get("name") or "").strip() or "Album"
    if band:
        return f"{band} — {name}"
    return name


def _normalize_token(value: str) -> str:
    value = value.replace("Ø", "O").replace("ø", "o")
    collapsed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in collapsed if not unicodedata.combining(ch))
    return "".join(ch for ch in without_marks.lower() if ch.isalnum())


def _find_band_logo_name(band: str) -> str | None:
    """Find a band logo file placed at ALBUMS_DIR root (same level as band folders)."""
    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    if not albums_dir or not band:
        return None

    root = Path(albums_dir)
    if not root.exists() or not root.is_dir():
        return None

    candidates = [
        f"{band}.jpg",
        f"{band}.jpeg",
        f"{band}.png",
        f"{band}.webp",
    ]
    for candidate in candidates:
        path = root / candidate
        if path.exists() and path.is_file():
            return candidate

    band_key = _normalize_token(band)
    if not band_key:
        return None

    for image_path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not image_path.is_file() or image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue

        cleaned_stem = re.sub(r"(?i)\b\d{3,5}x\d{3,5}\b", "", image_path.stem)
        cleaned_stem = re.sub(r"(?i)\blogo\b", "", cleaned_stem)
        image_key = _normalize_token(cleaned_stem)
        image_alpha = "".join(ch for ch in image_key if ch.isalpha())
        band_alpha = "".join(ch for ch in band_key if ch.isalpha())
        if (
            image_key == band_key
            or image_key.startswith(band_key)
            or band_key.startswith(image_key)
            or image_alpha == band_alpha
            or SequenceMatcher(None, image_alpha, band_alpha).ratio() >= 0.8
        ):
            return image_path.name
    return None


def _band_logo_src(band: str) -> str | None:
    logo_name = _find_band_logo_name(band)
    if logo_name:
        return _build_media_url("", logo_name)

    if _normalize_token(band) == "ormet":
        return "/ormet.jpeg"

    return None


def _album_track_names(album_folder: str, album_path: Path) -> list[str]:
    track_names = fetch_tracks_for_album(album_folder) if is_database_configured() else []
    if track_names:
        return track_names
    return [p.name for p in sorted(album_path.glob("*.wav"), key=lambda p: p.name.lower())]


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


def _is_admin() -> bool:
    """True when the logged-in user is listed in ADMIN_USERS (comma-separated usernames/emails)."""
    raw = os.environ.get("ADMIN_USERS", "").strip()
    if not raw:
        return False
    admins = {u.strip().lower() for u in raw.split(",") if u.strip()}
    user = session.get("user")
    if not user:
        return False
    identifiers = {
        (user.get("preferred_username") or "").lower(),
        (user.get("email") or "").lower(),
        (user.get("name") or "").lower(),
    }
    return bool(admins & identifiers)


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


def _current_base_url() -> str:
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured

    scheme = web.ctx.env.get("HTTP_X_FORWARDED_PROTO") or web.ctx.protocol.split(":", 1)[0]
    host = web.ctx.env.get("HTTP_X_FORWARDED_HOST") or web.ctx.env.get("HTTP_HOST") or web.ctx.host
    return f"{scheme}://{host}".rstrip("/")


def _payment_provider_badge(provider_code: str) -> str:
    if provider_code == "stripe":
        return "≡ƒîì Stripe"
    if provider_code == "mercadopago":
        return "≡ƒÆ│ Mercado Pago"
    return provider_code.title()


def _payment_provider_theme(provider_code: str) -> tuple[str, str, str]:
    if provider_code == "stripe":
        return ("#635BFF", "#ECEBFF", "Best for international buyers and cards")
    if provider_code == "mercadopago":
        return ("#009EE3", "#E8F7FF", "Best for Latin America and local payment methods")
    return ("#2a82d8", "#EEF6FF", "Hosted checkout")


def _preferred_payment_provider() -> str:
    language = (web.ctx.env.get("HTTP_ACCEPT_LANGUAGE", "") or "").lower()
    latam_markers = (
        "es-ar",
        "es-mx",
        "es-cl",
        "es-co",
        "es-pe",
        "es-uy",
        "es-py",
        "es-ec",
        "es-bo",
        "es-ve",
        "pt-br",
    )
    if any(marker in language for marker in latam_markers):
        return "mercadopago"
    return "stripe"


def _format_payment_amount(amount_total, currency: str, amount_is_minor: bool = False) -> str:
    if amount_total in (None, ""):
        return ""
    try:
        amount = float(amount_total)
    except (TypeError, ValueError):
        return web.websafe(str(amount_total))

    if amount_is_minor:
        amount = amount / 100.0
    return web.websafe(f"{currency.upper()} {amount:,.2f}")


def _is_remix_product(product: dict) -> bool:
    lowered = (product.get("name") or "").lower()
    if "ep" in lowered:
        return True
    if "is_remix" in product and product.get("is_remix"):
        return True
    return "remix" in lowered


def _release_sort_key(product: dict) -> tuple[int, int, str]:
    year = int(product.get("release_year") or 0)
    month = int(product.get("release_month") or 0)
    return -year, -month, (product.get("name") or "").lower()


def _release_label(product: dict) -> str:
    year = int(product.get("release_year") or 0)
    month = int(product.get("release_month") or 0)
    if year > 0 and 1 <= month <= 12:
        return f"{year:04d}-{month:02d}"
    if year > 0:
        return str(year)
    return ""


def _format_store_price(product: dict) -> str:
    usd = float(product.get("price_usd", product.get("price", 9.99)) or 9.99)
    ars = product.get("price_ars")
    if ars is None:
        rate = _get_daily_usd_ars_rate()
        ars = round(usd * rate, 2) if rate > 0 else None
    if ars is None:
        return web.websafe(f"USD ${usd:.2f}")
    try:
        ars_value = float(ars)
    except (TypeError, ValueError):
        return web.websafe(f"USD ${usd:.2f}")
    return web.websafe(f"USD ${usd:.2f} / ARS ${ars_value:,.2f}")


def _get_daily_usd_ars_rate() -> float:
    global _FX_CACHE_DATE, _FX_CACHE_RATE
    today = date.today()
    if _FX_CACHE_DATE == today and _FX_CACHE_RATE:
        return _FX_CACHE_RATE

    fallback = float(os.environ.get("USD_ARS_FALLBACK", "1100"))
    api_url = os.environ.get("USD_ARS_RATE_URL", "https://open.er-api.com/v6/latest/USD").strip()
    rate = fallback
    try:
        req = urllib.request.Request(api_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        rate = float((payload.get("rates") or {}).get("ARS") or fallback)
    except Exception:
        rate = fallback

    _FX_CACHE_DATE = today
    _FX_CACHE_RATE = rate
    return rate


def _build_purchase_delivery(product: dict) -> str:
    album_folder = product.get("folder")
    if not album_folder:
        return "<p>Your purchase has been confirmed.</p>"

    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    if not albums_dir:
        return "<p>Payment confirmed, but the album directory is not configured yet.</p>"

    root_path = Path(albums_dir).resolve()
    album_path = (root_path / album_folder).resolve()
    if root_path not in album_path.parents and album_path != root_path:
        return "<p>Payment confirmed, but the album path is invalid.</p>"
    if not album_path.exists() or not album_path.is_dir():
        return "<p>Payment confirmed, but the album files were not found.</p>"

    cover_html = ""
    jpgs = sorted(
        [p for p in [*album_path.glob("*.jpg"), *album_path.glob("*.jpeg")] if "3000x3000" in p.name.lower()],
        key=lambda p: p.name.lower(),
    )
    if jpgs:
        src = _build_media_url(album_folder, jpgs[0].name)
        cover_html = (
            '<p><span class="cd-case" style="display:inline-block;max-width:260px;">'
            '<span class="cd-spine"></span>'
            f'<img class="thumb" src="{src}" alt="{web.websafe(product["name"])}" width="240" loading="lazy" />'
            '</span></p>'
        )

    track_names = _album_track_names(album_folder, album_path)
    if not track_names:
        return cover_html + "<p>Payment confirmed, but there are no downloadable tracks for this album yet.</p>"

    rows = []
    for wav_name in track_names:
        src = _build_media_url(album_folder, wav_name)
        rows.append(
            "<li>"
            f"<div>{web.websafe(wav_name)}</div>"
            f"<div style=\"margin-top:6px;\"><a href=\"{src}\" download>Download track</a></div>"
            "</li>"
        )

    ttl_minutes = max(1, _get_media_url_ttl_seconds() // 60)
    return (
        cover_html
        +
        f"<p>Your secure download links are ready. They expire in about <strong>{ttl_minutes} minutes</strong>.</p>"
        "<ul>"
        + "".join(rows)
        + "</ul>"
    )


def find_product(product_id: str):
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product

    # Retry once in case catalog changed after startup/sync.
    _refresh_catalog()
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


def page(
    title: str,
    body: str,
    show_branding: bool = True,
    brand_subtitle: str = "Ormet",
    header_title: str | None = None,
    show_store_link: bool = True,
    show_spotify_link: bool = True,
    show_sync_link: bool = True,
    extra_css: str = "",
    extra_nav_links: list[tuple[str, str]] | None = None,
    extra_nav_select_options: list[tuple[str, str]] | None = None,
    extra_nav_select_current: str = "",
    extra_nav_select_label: str = "Choose file",
    extra_js: str = "",
) -> str:
    is_mobile = _is_mobile_request()
    device_class = "mobile" if is_mobile else "desktop"
    sync_label = "Sync discs" if is_mobile else "Sync discs to PostgreSQL"
    signed_in = _is_authenticated()
    whoami = web.websafe(_current_user_display_name()) if signed_in else ""
    header_text = header_title or title
    auth_nav = ""
    if _auth_enabled():
        if signed_in:
            auth_nav = f'<span style="margin-right:12px; color: var(--soft-ink);">{whoami}</span><a href="/logout">Logout</a>'
        else:
            auth_nav = '<a href="/login">Login</a>'

    nav_links = []
    if show_store_link:
        nav_links.append('<a href="/">Store</a>')
    # Spotify and Sync are admin-only ΓÇö only show to users listed in ADMIN_USERS
    if show_spotify_link and _is_admin():
        nav_links.append('<a href="/spotify">Spotify</a>')
    if show_sync_link and _is_admin():
        nav_links.append(f'<a href="/sync-discs">{sync_label}</a>')
    nav_links.append('<a href="/register">Sign up</a>')
    if extra_nav_links:
        for label, href in extra_nav_links:
            nav_links.append(f'<a href="{web.websafe(href)}">{web.websafe(label)}</a>')
    if extra_nav_select_options:
        options_html = []
        for label, href in extra_nav_select_options:
            selected_attr = " selected" if href == extra_nav_select_current else ""
            options_html.append(
                f'<option value="{web.websafe(href)}"{selected_attr}>{web.websafe(label)}</option>'
            )
        nav_links.append(
            '<form style="display:inline-flex;align-items:center;gap:6px;margin-right:12px;" onsubmit="return false;">'
            f'<label style="font-size:0.85rem;color:var(--soft-ink);">{web.websafe(extra_nav_select_label)}</label>'
            '<select onchange="if(this.value){window.location=this.value;}" '
            'style="padding:6px 8px;border-radius:8px;border:1px solid rgba(0,0,0,.2);max-width:240px;">'
            f'{"".join(options_html)}'
            '</select>'
            '</form>'
        )
    if auth_nav:
        nav_links.append(auth_nav)
    nav_html = "".join(nav_links)

    brand_block = (
        "<div class=\"brand\">"
        "<img class=\"logo\" src=\"/ormet.jpeg\" alt=\"Music Store Logo\" />"
        "<div>"
        f"<h1>{html.escape(header_text)}</h1>"
        f"<p style=\"margin: 4px 0 0 0; color: var(--soft-ink); font-size: 0.95rem; font-weight: 500;\">{html.escape(brand_subtitle)}</p>"
        "</div>"
        "</div>"
    ) if show_branding else f"<div><h1>{html.escape(header_text)}</h1></div>"

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
    .cd-case {{ position: relative; display: inline-block; margin: 0 0 10px 0; border-radius: 3px;
      background: #1c1c1e; padding: 6px 6px 6px 16px;
      box-shadow: 0 8px 24px rgba(0,0,0,.65), 0 2px 4px rgba(0,0,0,.45), inset 0 0 0 1px rgba(255,255,255,.10); }}
    .cd-spine {{ position: absolute; left: 0; top: 0; bottom: 0; width: 14px; border-radius: 3px 0 0 3px;
      background: linear-gradient(90deg, #080808 0%, #232323 35%, #181818 65%, #0b0b0b 100%);
      box-shadow: inset -1px 0 2px rgba(255,255,255,.07), inset 2px 0 0 rgba(255,255,255,.04); }}
    .cd-case::after {{ content: ''; position: absolute; inset: 6px 6px 6px 16px; border-radius: 2px;
      z-index: 2; pointer-events: none;
      background: linear-gradient(155deg,
        rgba(255,255,255,.22) 0%, rgba(255,255,255,.07) 28%,
        transparent 52%, rgba(0,0,0,.08) 100%);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,.18); }}
    .cd-case .thumb {{ display: block; margin: 0; border-radius: 1px; position: relative; z-index: 1; }}
      .title {{ margin: 12px 0 8px 0; font-weight: 800; font-size: clamp(0.9rem, 4vw, 1.1rem); color: #000000; text-transform: uppercase; word-wrap: break-word; overflow-wrap: break-word; }}
      .meta {{ margin: 0 0 12px 0; color: #1a1a1a; font-size: 1.2rem; font-weight: 700; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 14px; }}
    .kpi {{ background: rgba(255,255,255,0.72); border-radius: 12px; padding: 12px; }}
    .kpi-label {{ font-size: 0.82rem; color: var(--soft-ink); margin: 0 0 6px 0; }}
    .kpi-value {{ font-size: 1.35rem; font-weight: 800; margin: 0; color: var(--ink); }}
    .table-wrap {{ overflow-x: auto; margin: 10px 0 18px 0; }}
    table.data {{ width: 100%; border-collapse: collapse; background: rgba(255,255,255,0.72); border-radius: 12px; overflow: hidden; }}
    table.data th, table.data td {{ padding: 10px; text-align: left; border-bottom: 1px solid rgba(0,0,0,0.08); font-size: 0.92rem; }}
    table.data th {{ background: rgba(15,43,70,0.1); }}
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
                        {extra_css}
    </style>
  </head>
    <body class="{device_class}">
    <main class="page">
      <header class="hero">
                {brand_block}
        <nav class="nav" aria-label="Main">
                    {nav_html}
        </nav>
      </header>
      <section class="content">{body}</section>
    </main>
        {extra_js}
  </body>
</html>
"""


class Index:
    def GET(self):
        _refresh_catalog()
        web.header('Referrer-Policy', 'origin-when-cross-origin')
        book_css = """
            .band-flow { margin-bottom: 18px; padding: 20px; border-radius: 18px; background: linear-gradient(180deg, rgba(11,20,31,.92), rgba(28,39,56,.88)); box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 12px 28px rgba(0,0,0,.25); overflow: hidden; }
            .band-flow-title { margin: 0 0 14px 0; font-size: 1rem; letter-spacing: .08em; text-transform: uppercase; color: #d9e8ff; }
            .band-flow-hint { margin: -2px 0 12px 0; color: rgba(225,236,255,.78); font-size: .92rem; }
            .band-flow-track { display: flex; align-items: flex-end; justify-content: flex-start; gap: 18px; min-height: 270px; padding: 12px 8px 14px; perspective: 1400px; overflow-x: auto; overflow-y: hidden; scroll-behavior: smooth; cursor: grab; scrollbar-width: thin; scrollbar-color: rgba(181,207,255,.45) rgba(255,255,255,.08); }
            .band-flow-track.dragging { cursor: grabbing; scroll-behavior: auto; }
            .band-flow-track::-webkit-scrollbar { height: 10px; }
            .band-flow-track::-webkit-scrollbar-track { background: rgba(255,255,255,.08); border-radius: 999px; }
            .band-flow-track::-webkit-scrollbar-thumb { background: rgba(181,207,255,.45); border-radius: 999px; }
            .band-card { position: relative; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; width: min(220px, 28vw); min-width: 120px; min-height: 190px; padding: 14px; border-radius: 18px; text-decoration: none; color: #f6fbff; text-align: center; background: linear-gradient(180deg, rgba(255,255,255,.14), rgba(255,255,255,.06)); border: 1px solid rgba(255,255,255,.14); box-shadow: 0 10px 24px rgba(0,0,0,.35); transition: transform .25s ease, opacity .25s ease, box-shadow .25s ease, filter .25s ease; transform-origin: bottom center; }
            .band-card:hover { box-shadow: 0 14px 30px rgba(0,0,0,.4); }
            .band-card img { width: 96px; height: 96px; object-fit: cover; border-radius: 14px; display: block; margin: 0 auto 10px auto; box-shadow: 0 10px 24px rgba(0,0,0,.35); }
            .band-card.current { width: min(260px, 34vw); min-height: 230px; background: linear-gradient(180deg, rgba(76,138,214,.38), rgba(255,255,255,.12)); border-color: rgba(130,190,255,.45); transform: translateY(-8px) scale(1.04); z-index: 3; }
            .band-card.current img { width: 124px; height: 124px; }
            .band-card .band-name { font-weight: 800; }
            .band-card .band-meta { font-size: .85rem; color: rgba(230,240,255,.84); margin-top: 4px; }
            @media (max-width: 860px) {
                .band-flow-track { gap: 10px; min-height: 220px; padding-bottom: 14px; }
                .band-card, .band-card.current { width: 170px; min-width: 170px; min-height: 190px; transform: none; opacity: 1; filter: none; }
                .band-card.current { translate: none; }
            }
            @media (max-width: 560px) {
                .band-card, .band-card.current { width: 150px; min-width: 150px; }
            }
                """

        req = web.input(band="", band_page="1")
        selected_band = (req.band or "").strip()
        bands = sorted({(p.get("band") or "").strip() for p in PRODUCTS if (p.get("band") or "").strip()}, key=str.lower)
        try:
            requested_page = max(1, int((req.band_page or "1").strip()))
        except ValueError:
            requested_page = 1

        if bands:
            if selected_band and selected_band in bands:
                band_index = bands.index(selected_band)
            else:
                band_index = min(requested_page - 1, len(bands) - 1)
                selected_band = bands[band_index]
            band_page = band_index + 1
            total_band_pages = len(bands)
            selector_indexes = list(range(len(bands)))
        else:
            band_index = 0
            band_page = 1
            total_band_pages = 1
            selector_indexes = []
            selected_band = ""

        if selected_band:
            visible_products = [p for p in PRODUCTS if (p.get("band") or "").strip() == selected_band]
        else:
            visible_products = []

        albums_products = sorted([p for p in visible_products if not _is_remix_product(p)], key=_release_sort_key)
        remix_products = sorted([p for p in visible_products if _is_remix_product(p)], key=_release_sort_key)

        band_disk_counts: dict[str, int] = {}
        for product in PRODUCTS:
            band_name = (product.get("band") or "").strip()
            if not band_name:
                continue
            band_disk_counts[band_name] = band_disk_counts.get(band_name, 0) + 1

        selector_cards = []
        def band_page_href(page_number: int, band_name: str = "") -> str:
            query = {"band_page": str(page_number)}
            if band_name:
                query["band"] = band_name
            return "/?" + urllib.parse.urlencode(query)

        for idx in selector_indexes:
            band = bands[idx]
            href = band_page_href(idx + 1, band)
            flow_class = "band-card current" if idx == band_index else "band-card"

            logo_html = ""
            logo_src = _band_logo_src(band)
            if logo_src:
                safe_logo_src = web.websafe(logo_src)
                logo_html = (
                    f'<img src="{safe_logo_src}" alt="{web.websafe(band)} logo" '
                    'loading="lazy" />'
                )

            selector_cards.append(
                '<a class="{flow_class}" href="{href}">'
                '{logo_html}'
                '<div class="band-name">{label}</div>'
                '<div class="band-meta">{disk_count} disks</div>'
                '</a>'.format(
                    flow_class=flow_class,
                    href=href,
                    logo_html=logo_html,
                    label=web.websafe(band),
                    disk_count=band_disk_counts.get(band, 0),
                )
            )

        def render_cards(items: list[dict]) -> str:
            cards = []
            for product in items:
                pid = web.websafe(product["id"])
                name = web.websafe(product.get("name") or _product_display_name(product))
                price = _format_store_price(product)
                band_label = web.websafe((product.get("band") or "").strip())
                release = web.websafe(_release_label(product))

                thumb = ""
                if product.get("thumb") and product.get("folder"):
                    src = web.websafe(_build_media_url(product["folder"], product["thumb"]))
                    alt = name
                    thumb = (
                        "<div class=\"cd-case\">"
                        "<div class=\"cd-spine\"></div>"
                        f"<img class=\"thumb\" src=\"{src}\" alt=\"{alt}\" loading=\"lazy\" />"
                        "</div>"
                    )

                cards.append(
                    "<div class=\"card\">"
                    f"{thumb}"
                    f"<div style=\"font-size:.8rem;color:#1a1a1a;font-weight:700;min-height:1.1rem;\">{band_label}</div>"
                    f"<div class=\"title\">{name}</div>"
                    f"<div class=\"meta\">{price}</div>"
                    f"<div style=\"font-size:.82rem;color:#1a1a1a;margin:-8px 0 10px 0;\">{release}</div>"
                    "<div class=\"actions\">"
                    f"<a href=\"/album?id={pid}\">Details</a>"
                    f"<a href=\"/buy?id={pid}\">Buy</a>"
                    "</div>"
                    "</div>"
                )
            return "<div class=\"grid\">" + "".join(cards) + "</div>" if cards else ""

        body = (
            "<h2>Bands</h2>"
            + (
                '<div class="band-flow">'
                + (f'<h3 class="band-flow-title">Band picture flow — {web.websafe(selected_band)}</h3>' if selected_band else '<h3 class="band-flow-title">Band picture flow</h3>')
                + '<p class="band-flow-hint">Drag the strip with the mouse pointer, then click a band cover to open it.</p>'
                + '<div class="band-flow-track">'
                + ("".join(selector_cards) if selector_cards else '<p>This page is blank.</p>')
                + '</div></div>'
                if selector_cards
                else "<p>No bands available.</p>"
            )
            + (
                f"<h2>{web.websafe(selected_band)} disks</h2>"
                + "<p style=\"margin-top:-8px;color:#24384e;\">Prices are set in USD and ARS is refreshed daily from USD exchange rate.</p>"
                + "<div style=\"border:1px solid rgba(0,0,0,.12);border-radius:12px;padding:12px;margin-bottom:14px;background:rgba(255,255,255,.45);\">"
                + "<h3 style=\"margin:0 0 10px 0;\">Albums</h3>"
                + (render_cards(albums_products) if albums_products else "<p>No albums for this band yet.</p>")
                + "</div>"
                + "<div style=\"border:1px solid rgba(0,0,0,.12);border-radius:12px;padding:12px;background:rgba(255,255,255,.45);\">"
                + "<h3 style=\"margin:0 0 10px 0;\">Remixes</h3>"
                + (render_cards(remix_products) if remix_products else "<p>No remixes for this band yet.</p>")
                + "</div>"
                if selected_band
                else "<p>Choose a band to see its disks.</p>"
            )
        )
        flow_js = """
<script>
document.addEventListener('DOMContentLoaded', function () {
    const track = document.querySelector('.band-flow-track');
    if (!track) return;

    const current = track.querySelector('.band-card.current');
    if (current) {
        const left = current.offsetLeft - Math.max(0, (track.clientWidth - current.clientWidth) / 2);
        track.scrollLeft = Math.max(0, left);
    }

    let isPointerDown = false;
    let startX = 0;
    let startScrollLeft = 0;
    let dragged = false;

    track.addEventListener('pointerdown', function (event) {
        isPointerDown = true;
        dragged = false;
        startX = event.clientX;
        startScrollLeft = track.scrollLeft;
        track.classList.add('dragging');
        if (track.setPointerCapture) {
            track.setPointerCapture(event.pointerId);
        }
    });

    track.addEventListener('pointermove', function (event) {
        if (!isPointerDown) return;
        const delta = event.clientX - startX;
        if (Math.abs(delta) > 6) {
            dragged = true;
        }
        track.scrollLeft = startScrollLeft - delta;
    });

    function stopDragging(event) {
        if (!isPointerDown) return;
        isPointerDown = false;
        track.classList.remove('dragging');
        if (track.releasePointerCapture) {
            try {
                track.releasePointerCapture(event.pointerId);
            } catch (error) {
            }
        }
    }

    track.addEventListener('pointerup', stopDragging);
    track.addEventListener('pointercancel', stopDragging);
    track.addEventListener('mouseleave', function () {
        if (!isPointerDown) return;
        isPointerDown = false;
        track.classList.remove('dragging');
    });

    track.querySelectorAll('.band-card').forEach(function (card) {
        card.addEventListener('click', function (event) {
            if (!dragged) return;
            event.preventDefault();
            event.stopPropagation();
        });
    });
});
</script>
"""
        return page("Music Store", body, extra_css=book_css, extra_js=flow_js)


class Album:
    def GET(self):
        _refresh_catalog()
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

        track_names = _album_track_names(album_folder, album_path)
        if not track_names:
            return page("Album", "<p>No .wav files found for this album.</p>")

        cover = ""
        jpgs = sorted(
            [p for p in [*album_path.glob("*.jpg"), *album_path.glob("*.jpeg")] if "3000x3000" in p.name.lower()],
            key=lambda p: p.name.lower(),
        )
        if jpgs:
            src = _build_media_url(album_folder, jpgs[0].name)
            cover = (
                '<p><span class="cd-case" style="display:inline-block;max-width:260px;">'
                '<span class="cd-spine"></span>'
                f'<img class="thumb" src="{src}" alt="{web.websafe(product["name"])}" width="240" loading="lazy" />'
                '</span></p>'
            )

        rows = []
        for wav_name in track_names:
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
        req = web.input(id="", provider="")
        user_input_id = req.id
        provider = (req.provider or "").strip().lower()
        product = find_product(user_input_id)
        if not product:
            return page("Purchase", "<p>Unknown product.</p>")

        if provider:
            # Only require login when the user has chosen a payment method and is about to check out
            _require_authentication(next_path=web.ctx.fullpath)
            buyer_ref = (
                (session.get("user") or {}).get("email")
                or (session.get("user") or {}).get("preferred_username")
                or (session.get("user") or {}).get("name")
                or ""
            )
            checkout_url, error_message = create_checkout(provider, product, _current_base_url(), buyer_ref=buyer_ref)
            if error_message:
                body = (
                    f"<p>Could not start checkout for <strong>{web.websafe(product['name'])}</strong>.</p>"
                    f"<p>{web.websafe(error_message)}</p>"
                    f"<p><a href=\"/buy?id={web.websafe(product['id'])}\">Back to payment options</a></p>"
                )
                return page("Purchase", body)
            raise web.seeother(checkout_url)

        providers = configured_providers()
        if not providers:
            body = (
                f"<p><strong>{web.websafe(product['name'])}</strong></p>"
                "<p>Online purchases are not available yet ΓÇö please check back soon or contact us to arrange a purchase.</p>"
                f"<p><a href=\"/\">Back to store</a></p>"
            )
            return page("Purchase", body)

        safe_name = web.websafe(product["name"])
        safe_price = _format_store_price(product)
        preferred_provider = _preferred_payment_provider()
        providers = sorted(
            providers,
            key=lambda item: (0 if item["code"] == preferred_provider else 1, item["name"].lower()),
        )
        options = []
        for item in providers:
            accent, tint, fallback_note = _payment_provider_theme(item["code"])
            href = f"/buy?id={urllib.parse.quote(product['id'], safe='')}&provider={urllib.parse.quote(item['code'], safe='')}"
            recommended = item["code"] == preferred_provider
            recommended_badge = (
                '<div style="display:inline-block;margin-bottom:10px;padding:4px 10px;border-radius:999px;background:#111;color:#fff;font-size:0.72rem;font-weight:800;letter-spacing:.04em;">Recommended</div>'
                if recommended
                else ""
            )
            options.append(
                "<div class=\"card\" style=\"min-height:0;background:#fff;border:2px solid rgba(0,0,0,.08);box-shadow:0 10px 24px rgba(0,0,0,.12);\">"
                f"{recommended_badge}"
                f"<div style=\"display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;\">"
                f"<div class=\"title\" style=\"margin:0;color:{accent};\">{web.websafe(_payment_provider_badge(item['code']))}</div>"
                f"<div style=\"padding:6px 10px;border-radius:999px;background:{tint};color:{accent};font-weight:800;font-size:.78rem;\">Secure checkout</div>"
                "</div>"
                f"<div class=\"meta\" style=\"font-size:1rem;margin-bottom:8px;\">{web.websafe(item['description'])}</div>"
                f"<p style=\"margin:0 0 14px 0;color:#334;line-height:1.45;font-size:.95rem;\">{web.websafe(fallback_note)}</p>"
                "<div class=\"actions\">"
                f"<a href=\"{href}\" style=\"background:{accent};\">Continue with {web.websafe(item['name'])}</a>"
                "</div>"
                "</div>"
            )

        body = (
            f"<p>Select how you want to pay for <strong>{safe_name}</strong> ({safe_price}).</p>"
            "<p style=\"margin-top:-2px;color:#24384e;\">You can offer both gateways and let the buyer choose the most convenient option.</p>"
            "<div class=\"grid\">"
            + "".join(options)
            + "</div>"
        )
        return page("Purchase", body)


class CheckoutSuccess:
    def GET(self):
        req = web.input(id="", provider="")
        provider = (req.provider or "").strip().lower()
        expected_product_id = (req.id or "").strip()
        ok, message, details = verify_checkout(provider, dict(req), expected_product_id=expected_product_id)
        if not ok:
            body = (
                "<p>We could not confirm your payment yet.</p>"
                f"<p>{web.websafe(message)}</p>"
                f"<p><a href=\"/buy?id={web.websafe(expected_product_id)}\">Try checkout again</a></p>"
            )
            return page("Payment status", body)

        product_id = details.get("product_id") or expected_product_id
        product = PRODUCT_BY_ID.get(product_id)
        if not product:
            return page("Payment status", "<p>Payment confirmed, but the product could not be resolved.</p>")

        payment_rows = [
            ("Provider", details.get("provider_name") or provider.title()),
            ("Payment ID", str(details.get("payment_id") or "")),
            ("Status", str(details.get("status") or "")),
        ]
        if details.get("buyer"):
            payment_rows.append(("Buyer", str(details.get("buyer"))))
        if details.get("amount_total") not in (None, ""):
            payment_rows.append((
                "Amount",
                _format_payment_amount(
                    details.get("amount_total"),
                    str(details.get("currency") or ""),
                    bool(details.get("amount_is_minor")),
                ),
            ))

        table_rows = "".join(
            f"<tr><th>{web.websafe(label)}</th><td>{value if label == 'Amount' else web.websafe(value)}</td></tr>"
            for label, value in payment_rows
        )
        delivery_html = _build_purchase_delivery(product)
        body = (
            f"<p>Payment confirmed for <strong>{web.websafe(product['name'])}</strong>.</p>"
            "<div class=\"table-wrap\"><table class=\"data\">"
            f"{table_rows}"
            "</table></div>"
            f"{delivery_html}"
        )
        return page("Payment successful", body)


class CheckoutCancel:
    def GET(self):
        req = web.input(id="", provider="")
        product = PRODUCT_BY_ID.get((req.id or "").strip())
        product_name = web.websafe(product["name"]) if product else "this item"
        retry_link = f'/buy?id={web.websafe(product["id"])}' if product else "/"
        provider_name = _payment_provider_badge((req.provider or "").strip().lower()) if req.provider else "checkout"
        body = (
            f"<p>{web.websafe(provider_name)} checkout was cancelled for <strong>{product_name}</strong>.</p>"
            f"<p><a href=\"{retry_link}\">Choose another payment option</a></p>"
        )
        return page("Checkout cancelled", body)


class Media:
    def GET(self):
        req = web.input(album="", file="", exp="", sig="")
        album = (req.album or "").strip()
        file_name = (req.file or "").strip()
        exp_raw = (req.exp or "").strip()
        sig = (req.sig or "").strip()

        if not file_name or not exp_raw or not sig:
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
            result = sync_discs(albums_dir)
        except Exception as exc:
            return page("Sync discs", f"<p>Sync failed: {web.websafe(str(exc))}</p>")

        body = (
            "<p>Sync completed successfully.</p>"
            f"<p>Albums synced: <strong>{result['albums_synced']}</strong></p>"
            f"<p>Tracks synced: <strong>{result['tracks_synced']}</strong></p>"
        )
        return page("Sync discs", body)


class Spotify:
    def GET(self):
        selected_file = (web.input(file="").file or "").strip()
        title, body, file_menu = build_spotify_dashboard_body(selected_file=selected_file)
        selected_href = "/spotify"
        if selected_file:
            selected_href = f"/spotify?file={urllib.parse.quote(selected_file, safe='')}"

        menu_links = file_menu
        menu_select = None
        # If there are many files, use a rolling dropdown to keep navigation compact.
        if len(file_menu) > 10:
            menu_links = []
            menu_select = file_menu

        spotify_bg = (
            "body {"
            "background-image: url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140' viewBox='0 0 140 140'%3E%3Crect width='140' height='140' fill='%23121212'/%3E%3Ccircle cx='70' cy='70' r='48' fill='%231DB954'/%3E%3Cpath d='M41 58c19-6 39-4 57 6' stroke='%23121212' stroke-width='6' stroke-linecap='round' fill='none'/%3E%3Cpath d='M46 73c15-4 31-3 45 4' stroke='%23121212' stroke-width='5' stroke-linecap='round' fill='none'/%3E%3Cpath d='M51 86c12-3 23-2 33 3' stroke='%23121212' stroke-width='4' stroke-linecap='round' fill='none'/%3E%3C/svg%3E\");"
            "background-repeat: repeat;"
            "background-size: 140px 140px;"
            "background-attachment: fixed;"
            "}"
        )
        return page(
            title,
            body,
            show_branding=False,
            header_title="Spotify Info",
            show_store_link=False,
            show_spotify_link=False,
            show_sync_link=False,
            extra_css=spotify_bg,
            extra_nav_links=menu_links,
            extra_nav_select_options=menu_select,
            extra_nav_select_current=selected_href,
            extra_nav_select_label="Spotify file",
        )


class Register:
    def GET(self):
        req = web.input(done="")
        if req.done:
            body = (
                "<p style=\"font-size:1.3rem;\">Γ£à You're on the list!</p>"
                "<p>Thanks for signing up ΓÇö we'll send you news about new releases and exclusive offers.</p>"
                "<p><a href=\"/\">Back to store</a></p>"
            )
            return page("Mailing list", body)

        body = (
            "<h2>Stay in the loop</h2>"
            "<p>Sign up to hear about new releases, exclusive offers, and updates from Ormet.</p>"
            "<form method=\"post\" action=\"/register\" style=\"max-width:440px;\">"
            "<p><label style=\"font-weight:600;\">Name<br />"
            "<input name=\"name\" autocomplete=\"name\" required placeholder=\"Your name\""
            " style=\"width:100%;padding:10px;border-radius:8px;border:1px solid #b5b5b5;margin-top:4px;\" /></label></p>"
            "<p><label style=\"font-weight:600;\">Email<br />"
            "<input type=\"email\" name=\"email\" autocomplete=\"email\" required placeholder=\"you@example.com\""
            " style=\"width:100%;padding:10px;border-radius:8px;border:1px solid #b5b5b5;margin-top:4px;\" /></label></p>"
            "<p><button type=\"submit\" style=\"padding:11px 28px;border-radius:8px;border:none;"
            "background:#000;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;\">Subscribe</button></p>"
            "<p style=\"font-size:0.82rem;color:#555;\">No spam. Unsubscribe any time.</p>"
            "</form>"
        )
        return page("Mailing list", body)

    def POST(self):
        req = web.input(name="", email="")
        name = (req.name or "").strip()
        email = (req.email or "").strip().lower()

        if not name or not email or "@" not in email or "." not in email.split("@")[-1]:
            body = (
                "<p style=\"color:#8b0000;\">Please enter a valid name and email address.</p>"
                "<p><a href=\"/register\">Back</a></p>"
            )
            return page("Mailing list", body)

        ok, status = save_subscriber(name, email)
        if not ok:
            body = (
                "<p style=\"color:#8b0000;\">Sorry, we could not save your subscription right now. Please try again later.</p>"
                "<p><a href=\"/register\">Try again</a></p>"
            )
            return page("Mailing list", body)

        raise web.seeother("/register?done=1")


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
                "<p style=\"margin-top:18px;font-size:0.92rem;\">Just browsing? "
                "<a href=\"/register\" style=\"font-weight:700;\">Sign up to our mailing list</a> "
                "to get news about new releases.</p>"
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
    waitress_threads = int(os.environ.get("WAITRESS_THREADS", "12"))
    waitress_connection_limit = int(os.environ.get("WAITRESS_CONNECTION_LIMIT", "200"))
    serve(
        app.wsgifunc(),
        host="127.0.0.1",
        port=9000,
        threads=waitress_threads,
        connection_limit=waitress_connection_limit,
    )
