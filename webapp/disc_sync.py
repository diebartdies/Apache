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
                folder TEXT NOT NULL UNIQUE,
                thumb TEXT,
                price NUMERIC(10, 2) NOT NULL DEFAULT 9.99,
                track_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

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


def sync_discs(products: list[dict[str, Any]], albums_dir: str) -> dict[str, int]:
    dsn = _database_dsn()
    if not dsn:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* environment variables.")

    root = Path(albums_dir)
    if not albums_dir or not root.exists() or not root.is_dir():
        raise RuntimeError("ALBUMS_DIR is not available; cannot sync discs.")

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
                    INSERT INTO albums (album_id, name, folder, thumb, price, track_count, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (folder)
                    DO UPDATE SET
                        album_id = EXCLUDED.album_id,
                        name = EXCLUDED.name,
                        thumb = EXCLUDED.thumb,
                        price = EXCLUDED.price,
                        track_count = EXCLUDED.track_count,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        product.get("id", f"dir-{folder}"),
                        product.get("name", folder),
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
    from app import PRODUCTS  # Imported lazily to avoid circular import at module load.

    albums_dir = os.environ.get("ALBUMS_DIR", "").strip()
    result = sync_discs(PRODUCTS, albums_dir)
    print(f"Synced {result['albums_synced']} albums and {result['tracks_synced']} tracks to PostgreSQL.")


if __name__ == "__main__":
    main()
