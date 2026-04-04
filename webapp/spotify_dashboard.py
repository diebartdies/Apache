import html
from collections import Counter
import json
import os
from pathlib import Path
import urllib.parse


def _extract_track_label(item: object) -> str:
    if isinstance(item, str):
        return item

    if not isinstance(item, dict):
        return ""

    nested_track = item.get("track")
    if isinstance(nested_track, dict):
        item = nested_track

    title = (
        item.get("trackName")
        or item.get("master_metadata_track_name")
        or item.get("name")
        or item.get("title")
        or ""
    )

    artist = ""
    artists = item.get("artists")
    if isinstance(artists, list):
        artist_names = []
        for a in artists:
            if isinstance(a, dict):
                n = a.get("name") or a.get("artistName")
                if n:
                    artist_names.append(str(n))
            elif isinstance(a, str):
                artist_names.append(a)
        artist = ", ".join(artist_names)

    if not artist:
        artist = (
            item.get("artistName")
            or item.get("master_metadata_album_artist_name")
            or item.get("artist")
            or item.get("albumArtistName")
            or ""
        )

    if title and artist:
        return f"{artist} — {title}"
    if title:
        return str(title)
    if artist:
        return str(artist)
    return ""


def _extract_playlist_groups(payload: object) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []

    container = payload
    if isinstance(payload, dict):
        if isinstance(payload.get("playlists"), list):
            container = payload.get("playlists")
        elif isinstance(payload.get("items"), list):
            container = [{"name": payload.get("name") or "Playlist", "items": payload.get("items")}]

    if not isinstance(container, list):
        return groups

    for index, playlist in enumerate(container, start=1):
        if not isinstance(playlist, dict):
            continue

        playlist_name = (
            playlist.get("name")
            or playlist.get("playlistName")
            or playlist.get("title")
            or f"Playlist {index}"
        )

        raw_tracks = (
            playlist.get("items")
            or playlist.get("tracks")
            or playlist.get("entries")
            or []
        )
        tracks: list[str] = []
        if isinstance(raw_tracks, list):
            for t in raw_tracks:
                label = _extract_track_label(t)
                if label:
                    tracks.append(label)

        if not tracks:
            # Some exports use one-row-per-track where playlist name repeats.
            single_label = _extract_track_label(playlist)
            if single_label:
                tracks.append(single_label)

        groups.append({"name": str(playlist_name), "tracks": tracks})

    # If this appears to be a flat list of tracks, return it as one playlist.
    if not groups and isinstance(container, list):
        flat_tracks = []
        for row in container:
            label = _extract_track_label(row)
            if label:
                flat_tracks.append(label)
        if flat_tracks:
            groups.append({"name": "Playlist", "tracks": flat_tracks})

    # Merge duplicated playlist names (flat exports often repeat names per row).
    merged: dict[str, list[str]] = {}
    for g in groups:
        n = str(g.get("name", "Playlist"))
        merged.setdefault(n, [])
        merged[n].extend([str(t) for t in g.get("tracks", [])])

    return [{"name": name, "tracks": tracks} for name, tracks in merged.items()]


def _spotify_data_dir() -> str:
    return os.environ.get("SPOTIFY_DATA_DIR", "").strip()


def _infer_file_category(file_name: str) -> str:
    lower = file_name.lower()
    if "playlist" in lower:
        return "playlist"
    if "streaming" in lower:
        return "streaming history"
    if "search" in lower:
        return "search history"
    if "library" in lower:
        return "library"
    if "follow" in lower:
        return "following/followers"
    if "your" in lower:
        return "account export"
    return "generic"


def _format_hours_from_ms(total_ms: int) -> str:
    return f"{(total_ms / 1000 / 60 / 60):,.1f}"


def _spotify_summary() -> dict:
    data_dir = _spotify_data_dir()
    if not data_dir:
        return {
            "ok": False,
            "message": "SPOTIFY_DATA_DIR is not configured.",
            "path": "",
        }

    root = Path(data_dir)
    if not root.exists() or not root.is_dir():
        return {
            "ok": False,
            "message": f"Spotify data folder not found: {data_dir}",
            "path": data_dir,
        }

    json_files = sorted(root.rglob("*.json"), key=lambda p: str(p).lower())
    if not json_files:
        return {
            "ok": False,
            "message": f"No JSON files found under: {data_dir}",
            "path": data_dir,
        }

    file_rows = []
    total_records = 0
    playlist_rows = []
    artist_counter = Counter()
    track_counter = Counter()
    total_ms = 0

    for file_path in json_files:
        rel = str(file_path.relative_to(root)).replace("\\", "/")
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            file_rows.append({
                "file": rel,
                "records": "invalid json",
                "listening_hours": "0.0",
                "top_artists": [],
                "top_tracks": [],
            })
            continue

        row_count = 0
        file_artist_counter = Counter()
        file_track_counter = Counter()
        file_ms = 0
        common_key_counter = Counter()
        playlist_groups = _extract_playlist_groups(payload)
        file_category = _infer_file_category(rel)
        if isinstance(payload, list):
            row_count = len(payload)
            total_records += row_count
            for item in payload:
                if not isinstance(item, dict):
                    continue

                for key in item.keys():
                    common_key_counter[str(key)] += 1

                artist = (
                    item.get("artistName")
                    or item.get("master_metadata_album_artist_name")
                    or ""
                )
                track = (
                    item.get("trackName")
                    or item.get("master_metadata_track_name")
                    or ""
                )
                if artist:
                    artist_counter[str(artist)] += 1
                    file_artist_counter[str(artist)] += 1
                if artist and track:
                    track_counter[f"{artist} — {track}"] += 1
                    file_track_counter[f"{artist} — {track}"] += 1

                ms_val = item.get("msPlayed")
                if ms_val is None:
                    ms_val = item.get("ms_played")
                if isinstance(ms_val, (int, float)):
                    total_ms += int(ms_val)
                    file_ms += int(ms_val)

        elif isinstance(payload, dict):
            row_count = len(payload)
            total_records += row_count
            for key in payload.keys():
                common_key_counter[str(key)] += 1

        if file_path.name.lower().startswith("playlist"):
            playlist_rows.append({"name": file_path.stem, "items": row_count})

        file_rows.append(
            {
                "file": rel,
                "records": row_count,
                "listening_hours": _format_hours_from_ms(file_ms),
                "top_artists": file_artist_counter.most_common(6),
                "top_tracks": file_track_counter.most_common(6),
                "playlist_groups": playlist_groups,
                "category": file_category,
                "common_keys": [k for k, _ in common_key_counter.most_common(10)],
            }
        )

    files_by_records = sorted(
        file_rows,
        key=lambda r: r["records"] if isinstance(r["records"], int) else -1,
        reverse=True,
    )
    files_by_name = sorted(file_rows, key=lambda r: str(r["file"]).lower())

    return {
        "ok": True,
        "path": str(root),
        "json_files": len(json_files),
        "total_records": total_records,
        "listening_hours": _format_hours_from_ms(total_ms),
        "top_artists": artist_counter.most_common(12),
        "top_tracks": track_counter.most_common(12),
        "playlists": sorted(playlist_rows, key=lambda r: r["items"], reverse=True)[:20],
        "files": files_by_records[:30],
        "all_files": files_by_name,
    }


def _table_rows(pairs: list[tuple[str, int]]) -> str:
    out = []
    for name, count in pairs:
        out.append(f"<tr><td>{html.escape(str(name))}</td><td>{int(count):,}</td></tr>")
    if not out:
        out.append('<tr><td colspan="2">No data</td></tr>')
    return "".join(out)


def build_spotify_dashboard_body(selected_file: str = "") -> tuple[str, str, list[tuple[str, str]]]:
    summary = _spotify_summary()
    menu_options = [("All", "/spotify")]
    if summary.get("ok"):
        for row in summary.get("all_files", []):
            file_name = str(row.get("file", ""))
            if not file_name:
                continue
            menu_options.append((file_name, f"/spotify?file={urllib.parse.quote(file_name, safe='')}"))

    if not summary.get("ok"):
        message = html.escape(summary.get("message", "Unable to read Spotify data."))
        body = (
            "<h2>Spotify dashboard</h2>"
            f"<p>{message}</p>"
            "<p>Set <strong>SPOTIFY_DATA_DIR</strong> in environment and mount your folder into the container.</p>"
        )
        return "Spotify dashboard", body, menu_options

    kpis = (
        '<div class="kpi-grid">'
        f'<div class="kpi"><p class="kpi-label">JSON files</p><p class="kpi-value">{summary["json_files"]}</p></div>'
        f'<div class="kpi"><p class="kpi-label">Total records</p><p class="kpi-value">{summary["total_records"]:,}</p></div>'
        f'<div class="kpi"><p class="kpi-label">Listening hours</p><p class="kpi-value">{summary["listening_hours"]}</p></div>'
        "</div>"
    )

    artist_table = (
        '<h3 style="margin:0 0 8px 0;">Top artists</h3>'
        '<div class="table-wrap"><table class="data"><thead><tr><th>Artist</th><th>Plays</th></tr></thead><tbody>'
        f'{_table_rows(summary["top_artists"])}'
        "</tbody></table></div>"
    )

    track_table = (
        '<h3 style="margin:14px 0 8px 0;">Top tracks</h3>'
        '<div class="table-wrap"><table class="data"><thead><tr><th>Track</th><th>Plays</th></tr></thead><tbody>'
        f'{_table_rows(summary["top_tracks"])}'
        "</tbody></table></div>"
    )

    playlist_rows = "".join(
        f"<tr><td>{html.escape(p['name'])}</td><td>{int(p['items']):,}</td></tr>" for p in summary["playlists"]
    ) or '<tr><td colspan="2">No playlist files detected</td></tr>'
    playlist_table = (
        '<h3 style="margin:14px 0 8px 0;">Playlists</h3>'
        '<div class="table-wrap"><table class="data"><thead><tr><th>File</th><th>Items</th></tr></thead><tbody>'
        f"{playlist_rows}"
        "</tbody></table></div>"
    )

    file_rows = "".join(
        f"<tr><td>{html.escape(r['file'])}</td><td>{html.escape(str(r['records']))}</td></tr>" for r in summary["files"]
    )
    files_table = (
        '<h3 style="margin:14px 0 8px 0;">Largest JSON files (by records)</h3>'
        '<div class="table-wrap"><table class="data"><thead><tr><th>File</th><th>Records</th></tr></thead><tbody>'
        f"{file_rows}"
        "</tbody></table></div>"
    )

    selected_section = ""
    if selected_file:
        selected = next((r for r in summary.get("all_files", []) if str(r.get("file", "")) == selected_file), None)
        if selected:
            playlist_blocks = []
            for group in selected.get("playlist_groups", []):
                tracks = group.get("tracks", [])
                tracks_html = "".join(f"<li>{html.escape(str(t))}</li>" for t in tracks)
                if not tracks_html:
                    tracks_html = "<li><em>No songs found in this playlist.</em></li>"
                playlist_blocks.append(
                    f"<h4 style=\"margin:10px 0 6px 0;\">{html.escape(str(group.get('name', 'Playlist')))}</h4>"
                    f"<ol style=\"margin:0 0 14px 18px;\">{tracks_html}</ol>"
                )

            category = str(selected.get("category", "generic"))
            key_chips = "".join(
                f'<span style="display:inline-block;background:rgba(15,43,70,0.08);border-radius:14px;padding:4px 8px;margin:0 6px 6px 0;">{html.escape(k)}</span>'
                for k in selected.get("common_keys", [])
            ) or "<em>No structured keys detected.</em>"

            file_specific_block = (
                '<h3 style="margin:14px 0 8px 0;">File content type</h3>'
                '<div class="table-wrap" style="background: rgba(255,255,255,0.72); border-radius: 12px; padding: 12px;">'
                f'<p style="margin:0 0 8px 0;"><strong>Detected type:</strong> {html.escape(category)}</p>'
                f'<p style="margin:0 0 8px 0;"><strong>Main fields:</strong><br />{key_chips}</p>'
                '</div>'
            )

            playlists_as_is = ""
            if category == "playlist" and playlist_blocks:
                playlists_as_is = (
                    '<h3 style="margin:14px 0 8px 0;">Playlists and songs (as exported)</h3>'
                    '<div class="table-wrap" style="background: rgba(255,255,255,0.72); border-radius: 12px; padding: 12px;">'
                    f"{''.join(playlist_blocks)}"
                    "</div>"
                )

            selected_section = (
                '<h3 style="margin:14px 0 8px 0;">Selected file details</h3>'
                '<div class="kpi-grid">'
                f'<div class="kpi"><p class="kpi-label">File</p><p class="kpi-value" style="font-size:1rem;">{html.escape(str(selected.get("file", "")))}</p></div>'
                f'<div class="kpi"><p class="kpi-label">Records</p><p class="kpi-value">{html.escape(str(selected.get("records", 0)))}</p></div>'
                f'<div class="kpi"><p class="kpi-label">Listening hours</p><p class="kpi-value">{html.escape(str(selected.get("listening_hours", "0.0")))}</p></div>'
                '</div>'
                f'{file_specific_block}'
                f'{playlists_as_is}'
            )
        else:
            selected_section = f'<p><em>Selected file not found: {html.escape(selected_file)}</em></p>'

    if selected_file:
        body = (
            "<h2>Spotify dashboard</h2>"
            f"<p style=\"margin-top:0;color:#24445f;\"><strong>Source:</strong> {html.escape(summary['path'])}</p>"
            f"{selected_section}"
        )
    else:
        body = (
            "<h2>Spotify dashboard</h2>"
            f"<p style=\"margin-top:0;color:#24445f;\"><strong>Source:</strong> {html.escape(summary['path'])}</p>"
            f"{kpis}{artist_table}{track_table}{playlist_table}{files_table}"
        )
    return "Spotify dashboard", body, menu_options
