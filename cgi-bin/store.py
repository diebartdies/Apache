#!/usr/bin/env python3

import html
import os
import sys
from pathlib import Path
import urllib.parse

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


def _find_product(product_id: str):
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    return None


def _render_page(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <title>{html.escape(title)}</title>
        <style>
            body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
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
    <p><a href=\"/\">Home</a> | <a href=\"/cgi-bin/store.py\">Store</a> | <a href=\"/cgi-bin/store.py?action=sync\">Sync discs to PostgreSQL</a></p>
    {body_html}
  </body>
</html>
"""


def _sync_discs_to_postgres() -> str:
    webapp_dir = (Path(__file__).resolve().parents[1] / "webapp").as_posix()
    if webapp_dir not in sys.path:
        sys.path.insert(0, webapp_dir)

    try:
        from disc_sync import is_database_configured, sync_discs
    except Exception as exc:
        return _render_page("Sync discs", f"<p>Sync failed: {html.escape(str(exc))}</p>")

    if not is_database_configured():
        return _render_page(
            "Sync discs",
            "<p>PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* environment variables.</p>",
        )

    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    try:
        result = sync_discs(PRODUCTS, albums_dir)
    except Exception as exc:
        return _render_page("Sync discs", f"<p>Sync failed: {html.escape(str(exc))}</p>")

    body = (
        "<p>Sync completed successfully.</p>"
        f"<p>Albums synced: <strong>{result['albums_synced']}</strong></p>"
        f"<p>Tracks synced: <strong>{result['tracks_synced']}</strong></p>"
    )
    return _render_page("Sync discs", body)


def main() -> None:
    query = urllib.parse.parse_qs(
        ("" if "QUERY_STRING" not in __import__("os").environ else __import__("os").environ["QUERY_STRING"]),
        keep_blank_values=True,
    )

    action: str = (query.get("action", [""])[0] or "list").lower()
    product_id = query.get("id", [""])[0]

    if action == "sync":
        page = _sync_discs_to_postgres()
    elif action == "buy" and product_id:
        product = _find_product(product_id)
        if not product:
            body = "<p>Unknown product.</p>"
            page = _render_page("Purchase", body)
        else:
            safe_name = html.escape(product["name"])
            safe_price = html.escape(f"${product['price']:.2f}")
            body = (
                f"<p>Order received for <strong>{safe_name}</strong> ({safe_price}).</p>"
                "<p>This is a demo checkout (no payment processing).</p>"
            )
            page = _render_page("Purchase", body)
    elif action == "detail" and product_id:
        product = _find_product(product_id)
        if not product:
            page = _render_page("Album", "<p>Unknown album.</p>")
        else:
            albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
            if not albums_dir:
                page = _render_page("Album", "<p>Album directory not configured (ALBUMS_DIR is empty).</p>")
            else:
                album_folder = product.get("folder") or product["name"]
                album_path = (Path(albums_dir) / album_folder).resolve()
                root_path = Path(albums_dir).resolve()
                if root_path not in album_path.parents and album_path != root_path:
                    page = _render_page("Album", "<p>Invalid album path.</p>")
                elif not album_path.exists() or not album_path.is_dir():
                    page = _render_page("Album", "<p>Album folder not found.</p>")
                else:
                    wavs = sorted(album_path.glob("*.wav"), key=lambda p: p.name.lower())
                    if not wavs:
                        page = _render_page("Album", "<p>No .wav files found for this album.</p>")
                    else:
                        album_url = urllib.parse.quote(album_folder, safe="")

                        cover = ""
                        jpgs = sorted(
                            [p for p in [*album_path.glob("*.jpg"), *album_path.glob("*.jpeg")] if "3000x3000" in p.name.lower()],
                            key=lambda p: p.name.lower(),
                        )
                        if jpgs:
                            img_url = urllib.parse.quote(jpgs[0].name, safe="")
                            src = f"/albums/{album_url}/{img_url}"
                            cover = f"<p><img src=\"{src}\" alt=\"{html.escape(product['name'])}\" width=\"240\" loading=\"lazy\" /></p>"

                        rows = []
                        for wav in wavs:
                            wav_name = wav.name
                            wav_url = urllib.parse.quote(wav_name, safe="")
                            safe_label = html.escape(wav_name)
                            src = f"/albums/{album_url}/{wav_url}"
                            rows.append(
                                "<li>"
                                f"<div>{safe_label}</div>"
                                f"<audio controls preload=\"none\" src=\"{src}\"></audio>"
                                "</li>"
                            )
                        body = f"<h2>{html.escape(product['name'])}</h2>" + cover + "<ul>" + "".join(rows) + "</ul>"
                        page = _render_page("Album", body)
    else:
        cards = []
        note = "" if not PRODUCTS_NOTE else f"<p><em>{html.escape(PRODUCTS_NOTE)}</em></p>"
        for product in PRODUCTS:
            pid = urllib.parse.quote(product["id"], safe="")
            name = html.escape(product["name"])
            price = html.escape(f"${product['price']:.2f}")

            thumb = ""
            if product.get("thumb"):
                src = html.escape(product["thumb"])
                alt = name
                thumb = f"<img class=\"thumb\" src=\"{src}\" alt=\"{alt}\" loading=\"lazy\" />"

            cards.append(
                "<div class=\"card\">"
                f"{thumb}"
                f"<div class=\"title\">{name}</div>"
                f"<div class=\"meta\">{price}</div>"
                "<div class=\"actions\">"
                f"<a href=\"/cgi-bin/store.py?action=detail&id={pid}\">Details</a>"
                f"<a href=\"/cgi-bin/store.py?action=buy&id={pid}\">Buy</a>"
                "</div>"
                "</div>"
            )

        body = note + "<h2>Albums</h2><div class=\"grid\">" + "".join(cards) + "</div>"
        page = _render_page("Music Store", body)

    print("Content-Type: text/html")
    print()
    print(page)


if __name__ == "__main__":
    main()
