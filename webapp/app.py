import html
import os
from pathlib import Path
import urllib.parse

import web
from waitress import serve

from disc_sync import is_database_configured, sync_discs

urls = (
    "/", "Index",
    "/buy", "Buy",
    "/album", "Album",
    "/sync-discs", "SyncDiscs",
)

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
                album_url = urllib.parse.quote(entry.name, safe="")
                img_url = urllib.parse.quote(jpgs[0].name, safe="")
                thumb_url = f"/albums/{album_url}/{img_url}"

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


def find_product(product_id: str):
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    return None


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <title>{html.escape(title)}</title>
        <style>
            body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background-color: #f0f0f0; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; padding: 0; }}
            .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; }}
            .thumb {{ width: 100%; max-width: 300px; height: auto; display: block; border-radius: 6px; }}
            .title {{ margin: 10px 0 6px; font-weight: 600; }}
            .meta {{ margin: 0 0 10px; }}
            .actions a {{ margin-right: 10px; }}
        </style>
  </head>
  <body>
    <h1>{html.escape(title)}</h1>
        <p><a href="/">Store</a> | <a href="/sync-discs">Sync discs to PostgreSQL</a></p>
    {body}
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
            if product.get("thumb"):
                src = web.websafe(product["thumb"])
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

        album_url = urllib.parse.quote(album_folder, safe="")

        cover = ""
        jpgs = sorted(
            [p for p in [*album_path.glob("*.jpg"), *album_path.glob("*.jpeg")] if "3000x3000" in p.name.lower()],
            key=lambda p: p.name.lower(),
        )
        if jpgs:
            img_url = urllib.parse.quote(jpgs[0].name, safe="")
            src = f"/albums/{album_url}/{img_url}"
            cover = f"<p><img src=\"{src}\" alt=\"{web.websafe(product['name'])}\" width=\"240\" loading=\"lazy\" /></p>"

        rows = []
        for wav in wavs:
            wav_name = wav.name
            wav_url = urllib.parse.quote(wav_name, safe="")
            safe_label = web.websafe(wav_name)
            src = f"/albums/{album_url}/{wav_url}"
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


class SyncDiscs:
    def GET(self):
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


app = web.application(urls, globals())

if __name__ == "__main__":
    serve(app.wsgifunc(), host="127.0.0.1", port=9000)
