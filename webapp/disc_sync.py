import os
import importlib
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
    return {
        "id": f"dir-{safe_id}",
        "name": album_path.name,
        "band": band,
        "folder": relative,
        "thumb": _first_cover_name(album_path),
        "price": 9.99,
    }


def scan_products_from_dir(albums_dir: str) -> tuple[list[dict[str, Any]], str]:
    root = Path(albums_dir)
    if not albums_dir or not root.exists() or not root.is_dir():
        return [], f"Configured ALBUMS_DIR not found: {albums_dir}"

    products: list[dict[str, Any]] = []

    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue

        subdirs = sorted([p for p in entry.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
        if subdirs:
            # New structure: /albums/<band>/<album>
            for album_dir in subdirs:
                products.append(_product_from_album_path(root, album_dir, band=entry.name))
            continue

        # Backward-compatible structure: /albums/<album>
        products.append(_product_from_album_path(root, entry, band=""))

    if not products:
        return [], f"No album folders found in: {albums_dir}"

    return products, f"Loaded {len(products)} albums from: {albums_dir}"


def fetch_products_from_db() -> list[dict[str, Any]]:
    dsn = _database_dsn()
    if not dsn:
        return []

    psycopg = _load_psycopg()
    with psycopg.connect(dsn) as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT album_id, name, COALESCE(band, ''), folder, thumb, price, track_count
                FROM albums
                ORDER BY NULLIF(band, ''), name
                """
            )
            rows = cur.fetchall()

    products: list[dict[str, Any]] = []
    for album_id, name, band, folder, thumb, price, track_count in rows:
        products.append(
            {
                "id": album_id,
                "name": name,
                "band": band,
                "folder": folder,
                "thumb": thumb,
                "price": float(price),
                "track_count": int(track_count),
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
    psycopg = _load_psycopg()

    with psycopg.connect(dsn) as conn:
        _ensure_schema(conn)

        with conn.cursor() as cur:
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
                cur.execute(
                    """
                    INSERT INTO albums (album_id, name, band, folder, thumb, price, track_count, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (folder)
                    DO UPDATE SET
                        album_id = EXCLUDED.album_id,
                        name = EXCLUDED.name,
                        band = EXCLUDED.band,
                        thumb = EXCLUDED.thumb,
                        price = EXCLUDED.price,
                        track_count = EXCLUDED.track_count,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        product.get("id", f"dir-{folder}"),
                        product.get("name", folder),
                        product.get("band", ""),
                        folder,
                        product.get("thumb"),
                        product.get("price", 9.99),
                        len(wavs),
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
