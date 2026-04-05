import os
import importlib
import json
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


def _database_dsn() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return database_url

    host = os.environ.get("POSTGRES_HOST", "").strip()
    port = os.environ.get("POSTGRES_PORT", "5432").strip()
    dbname = os.environ.get("POSTGRES_DB", "").strip()
    user = os.environ.get("POSTGRES_USER", "").strip()
    password = os.environ.get("POSTGRES_PASSWORD", "").strip()

    if not all([host, dbname, user, password]):
        return ""

    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


def is_database_configured() -> bool:
    return bool(_database_dsn())


def _load_psycopg() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is not installed in this environment.") from exc


def _ensure_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS albums (
                id BIGSERIAL PRIMARY KEY,
                album_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                band TEXT,
                folder TEXT NOT NULL UNIQUE,
                thumb TEXT,
                price NUMERIC(10, 2) NOT NULL DEFAULT 9.99,
                track_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        cur.execute("ALTER TABLE albums ADD COLUMN IF NOT EXISTS band TEXT")
        cur.execute("ALTER TABLE albums ADD COLUMN IF NOT EXISTS price_usd NUMERIC(10, 2) NOT NULL DEFAULT 9.99")
        cur.execute("ALTER TABLE albums ADD COLUMN IF NOT EXISTS price_ars NUMERIC(12, 2)")
        cur.execute("ALTER TABLE albums ADD COLUMN IF NOT EXISTS fx_ars_per_usd NUMERIC(12, 4)")
        cur.execute("ALTER TABLE albums ADD COLUMN IF NOT EXISTS release_year INTEGER")
        cur.execute("ALTER TABLE albums ADD COLUMN IF NOT EXISTS release_month INTEGER")
        cur.execute("ALTER TABLE albums ADD COLUMN IF NOT EXISTS is_remix BOOLEAN NOT NULL DEFAULT FALSE")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id BIGSERIAL PRIMARY KEY,
                album_id BIGINT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                file_size_bytes BIGINT NOT NULL,
                UNIQUE(album_id, filename)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fx_rates (
                quote_date DATE PRIMARY KEY,
                usd_ars NUMERIC(12, 4) NOT NULL,
                source TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def _fetch_usd_ars_rate() -> tuple[float, str]:
    api_url = os.environ.get("USD_ARS_RATE_URL", "https://open.er-api.com/v6/latest/USD").strip()
    with urllib.request.urlopen(api_url, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rates = payload.get("rates") or {}
    ars = rates.get("ARS")
    if ars is None:
        raise RuntimeError("Could not find ARS rate in exchange-rate response.")
    return float(ars), api_url


def _get_or_refresh_usd_ars_rate(conn: Any) -> float:
    today = date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT usd_ars FROM fx_rates WHERE quote_date = %s", (today,))
        row = cur.fetchone()
        if row:
            return float(row[0])

    fallback_rate = float(os.environ.get("USD_ARS_FALLBACK", "1100"))
    rate = fallback_rate
    source = "fallback"
    try:
        rate, source = _fetch_usd_ars_rate()
    except Exception:
        with conn.cursor() as cur:
            cur.execute("SELECT usd_ars FROM fx_rates ORDER BY quote_date DESC LIMIT 1")
            latest = cur.fetchone()
            if latest:
                rate = float(latest[0])
                source = "cached"

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fx_rates (quote_date, usd_ars, source, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (quote_date)
            DO UPDATE SET usd_ars = EXCLUDED.usd_ars, source = EXCLUDED.source, updated_at = NOW()
            """,
            (today, rate, source),
        )
    return rate


def _release_year_month(album_path: Path) -> tuple[int, int]:
    import re
    m = re.search(r"(19|20)\d{2}[^\d]?(0[1-9]|1[0-2])", album_path.name)
    if m:
        year = int(m.group(0)[:4])
        month = int(m.group(2))
        return year, month

    candidates = [*album_path.glob("*.wav"), *album_path.glob("*.jpg"), *album_path.glob("*.jpeg")]
    if not candidates:
        stat = album_path.stat()
        tm = stat.st_mtime
    else:
        tm = min(p.stat().st_mtime for p in candidates)

    from datetime import datetime
    dt = datetime.fromtimestamp(tm)
    return dt.year, dt.month


def _is_remix_name(name: str) -> bool:
    return "remix" in (name or "").lower()


def _price_usd_for_album(name: str, track_count: int) -> float:
    lowered = (name or "").lower()
    is_ep = "ep" in lowered
    if _is_remix_name(name) or (is_ep and track_count < 4):
        return 5.75
    return 9.99


def _first_cover_name(album_path: Path) -> str | None:
    preferred = sorted(
        [p for p in [*album_path.glob("*.jpg"), *album_path.glob("*.jpeg")] if "3000x3000" in p.name.lower()],
        key=lambda p: p.name.lower(),
    )
    if preferred:
        return preferred[0].name

    any_cover = sorted([*album_path.glob("*.jpg"), *album_path.glob("*.jpeg")], key=lambda p: p.name.lower())
    if any_cover:
        return any_cover[0].name
    return None


def _product_from_album_path(root: Path, album_path: Path, band: str = "") -> dict[str, Any]:
    relative = album_path.relative_to(root).as_posix()
    safe_id = relative.replace("/", "__")
    track_count = len(list(album_path.glob("*.wav")))
    release_year, release_month = _release_year_month(album_path)
    price_usd = _price_usd_for_album(album_path.name, track_count)
    return {
        "id": f"dir-{safe_id}",
        "name": album_path.name,
        "band": band,
        "folder": relative,
        "thumb": _first_cover_name(album_path),
        "price": price_usd,
        "price_usd": price_usd,
        "price_ars": None,
        "track_count": track_count,
        "release_year": release_year,
        "release_month": release_month,
        "is_remix": _is_remix_name(album_path.name),
    }


def _is_album_dir(path: Path) -> bool:
    """A real album folder must contain at least one WAV track."""
    if path.name.startswith("NotAlbun"):
        return False
    return any(path.glob("*.wav"))


def scan_products_from_dir(albums_dir: str) -> tuple[list[dict[str, Any]], str]:
    root = Path(albums_dir)
    if not albums_dir or not root.exists() or not root.is_dir():
        return [], f"Configured ALBUMS_DIR not found: {albums_dir}"

    products: list[dict[str, Any]] = []

    # Strict structure only: /albums/<band>/<album>
    for band_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        album_dirs = sorted([p for p in band_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
        for album_dir in album_dirs:
            if not _is_album_dir(album_dir):
                continue
            products.append(_product_from_album_path(root, album_dir, band=band_dir.name))

    if not products:
        return [], f"No band/album folders found in: {albums_dir}"

    return products, f"Loaded {len(products)} albums from: {albums_dir}"


def fetch_products_from_db() -> list[dict[str, Any]]:
    dsn = _database_dsn()
    if not dsn:
        return []

    psycopg = _load_psycopg()
    with psycopg.connect(dsn) as conn:
        _ensure_schema(conn)
        usd_ars_rate = _get_or_refresh_usd_ars_rate(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE albums
                SET price_ars = ROUND(price_usd * %s, 2),
                    fx_ars_per_usd = %s
                """,
                (usd_ars_rate, usd_ars_rate),
            )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT album_id, name, COALESCE(band, ''), folder, thumb,
                       price_usd, price_ars, track_count,
                       COALESCE(release_year, EXTRACT(YEAR FROM updated_at)::INT),
                       COALESCE(release_month, EXTRACT(MONTH FROM updated_at)::INT),
                       COALESCE(is_remix, FALSE)
                FROM albums
                ORDER BY NULLIF(band, ''),
                         COALESCE(release_year, EXTRACT(YEAR FROM updated_at)::INT),
                         COALESCE(release_month, EXTRACT(MONTH FROM updated_at)::INT),
                         name
                """
            )
            rows = cur.fetchall()

    products: list[dict[str, Any]] = []
    for album_id, name, band, folder, thumb, price_usd, price_ars, track_count, release_year, release_month, is_remix in rows:
        products.append(
            {
                "id": album_id,
                "name": name,
                "band": band,
                "folder": folder,
                "thumb": thumb,
                "price": float(price_usd),
                "price_usd": float(price_usd),
                "price_ars": float(price_ars) if price_ars is not None else None,
                "track_count": int(track_count),
                "release_year": int(release_year),
                "release_month": int(release_month),
                "is_remix": bool(is_remix),
            }
        )
    return products


def fetch_tracks_for_album(folder: str) -> list[str]:
    dsn = _database_dsn()
    if not dsn:
        return []

    psycopg = _load_psycopg()
    with psycopg.connect(dsn) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.filename
                FROM tracks t
                JOIN albums a ON a.id = t.album_id
                WHERE a.folder = %s
                ORDER BY t.filename
                """,
                (folder,),
            )
            return [row[0] for row in cur.fetchall()]


def sync_discs(albums_dir: str) -> dict[str, int]:
    dsn = _database_dsn()
    if not dsn:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* environment variables.")

    root = Path(albums_dir)
    if not albums_dir or not root.exists() or not root.is_dir():
        raise RuntimeError("ALBUMS_DIR is not available; cannot sync discs.")

    products, _ = scan_products_from_dir(albums_dir)

    album_count = 0
    track_count = 0
    scanned_folders = {p.get("folder") for p in products if p.get("folder")}
    psycopg = _load_psycopg()

    with psycopg.connect(dsn) as conn:
        _ensure_schema(conn)
        usd_ars_rate = _get_or_refresh_usd_ars_rate(conn)

        with conn.cursor() as cur:
            # Remove albums that no longer exist in the current scan.
            if scanned_folders:
                cur.execute(
                    "DELETE FROM albums WHERE folder <> ALL(%s)",
                    (list(scanned_folders),),
                )

            for product in products:
                folder = product.get("folder")
                if not folder:
                    continue

                album_path = (root / folder).resolve()
                root_path = root.resolve()
                if root_path not in album_path.parents and album_path != root_path:
                    continue
                if not album_path.exists() or not album_path.is_dir():
                    continue

                wavs = sorted(album_path.glob("*.wav"), key=lambda p: p.name.lower())
                price_usd = _price_usd_for_album(product.get("name", folder), len(wavs))
                price_ars = round(price_usd * usd_ars_rate, 2)
                cur.execute(
                    """
                    INSERT INTO albums (
                        album_id, name, band, folder, thumb,
                        price, price_usd, price_ars, fx_ars_per_usd,
                        track_count, release_year, release_month, is_remix, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (folder)
                    DO UPDATE SET
                        album_id = EXCLUDED.album_id,
                        name = EXCLUDED.name,
                        band = EXCLUDED.band,
                        thumb = EXCLUDED.thumb,
                        price = EXCLUDED.price,
                        price_usd = EXCLUDED.price_usd,
                        price_ars = EXCLUDED.price_ars,
                        fx_ars_per_usd = EXCLUDED.fx_ars_per_usd,
                        track_count = EXCLUDED.track_count,
                        release_year = EXCLUDED.release_year,
                        release_month = EXCLUDED.release_month,
                        is_remix = EXCLUDED.is_remix,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        product.get("id", f"dir-{folder}"),
                        product.get("name", folder),
                        product.get("band", ""),
                        folder,
                        product.get("thumb"),
                        price_usd,
                        price_usd,
                        price_ars,
                        usd_ars_rate,
                        len(wavs),
                        product.get("release_year"),
                        product.get("release_month"),
                        bool(product.get("is_remix", False)),
                    ),
                )
                album_row = cur.fetchone()
                if not album_row:
                    continue

                db_album_id = album_row[0]
                cur.execute("DELETE FROM tracks WHERE album_id = %s", (db_album_id,))

                for wav in wavs:
                    size_bytes = wav.stat().st_size
                    cur.execute(
                        """
                        INSERT INTO tracks (album_id, filename, file_size_bytes)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (album_id, filename)
                        DO UPDATE SET file_size_bytes = EXCLUDED.file_size_bytes
                        """,
                        (db_album_id, wav.name, size_bytes),
                    )

                album_count += 1
                track_count += len(wavs)

        conn.commit()

    return {"albums_synced": album_count, "tracks_synced": track_count}


def save_subscriber(name: str, email: str) -> tuple[bool, str]:
    """Insert a newsletter subscriber into the subscribers table.

    Returns (True, "subscribed") on new signup, (True, "already_subscribed") if the email
    already exists, or (False, error_message) on failure.
    """
    dsn = _database_dsn()
    if not dsn:
        return False, "Subscription list is not available right now."

    psycopg = _load_psycopg()
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscribers (
                        id          BIGSERIAL PRIMARY KEY,
                        name        TEXT        NOT NULL,
                        email       TEXT        NOT NULL UNIQUE,
                        subscribed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "INSERT INTO subscribers (name, email) VALUES (%s, %s)"
                    " ON CONFLICT (email) DO NOTHING RETURNING id",
                    (name.strip(), email.strip().lower()),
                )
                row = cur.fetchone()
            conn.commit()
        return (True, "subscribed") if row else (True, "already_subscribed")
    except Exception as exc:
        return False, str(exc)


def main() -> None:
    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    result = sync_discs(albums_dir)
    print(f"Synced {result['albums_synced']} albums and {result['tracks_synced']} tracks to PostgreSQL.")


if __name__ == "__main__":
    main()
