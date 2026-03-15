# Copilot instructions (Apache + Python)

## Big picture
- Single Docker container runs **Apache httpd** plus a **Python web.py app**.
- Apache serves **Python CGI** scripts under `/cgi-bin/` and **reverse-proxies everything else** to the web.py app.
  - CGI enablement: [conf/extra/python-cgi.conf](conf/extra/python-cgi.conf)
  - web.py app: [webapp/app.py](webapp/app.py)
  - CGI example: [cgi-bin/hello.py](cgi-bin/hello.py)

## How requests flow
- Browser → `http://localhost:8585/` → Apache → proxied to `http://127.0.0.1:9000/` → waitress/web.py.
- Browser → `http://localhost:8585/cgi-bin/*.py` → Apache CGI executes the script directly.

## Dev workflow (Docker)
- Build + run: `docker compose up --build`
- Open:
  - `/` (web.py store): `http://localhost:8585/`
  - CGI hello: `http://localhost:8585/cgi-bin/hello.py`
  - CGI store: `http://localhost:8585/cgi-bin/store.py`
- Logs: `docker compose logs -f`

## Albums source (host folder)
- The store can optionally load “albums” from a directory specified by `ALBUMS_DIR` (looks for subfolders; each subfolder becomes an album).
- In Docker, `ALBUMS_DIR` must be a **path inside the container** (commonly `/opt/albums`). Mount your host folder/share to that container path via Compose.
  - This repo uses `ALBUMS_HOST_PATH` in [compose.yaml](compose.yaml) to control what gets mounted to `/opt/albums` (see [.env.example](.env.example)).
  - If the directory isn’t mounted/available, the app falls back to the demo products list.
- If Docker can’t read the UNC share directly, use [sync-albums.ps1](sync-albums.ps1) to mirror album folders (and optionally `.wav` files) into [albums/](albums/) and mount that local folder instead.

## Project conventions / gotchas
- Python deps are installed into `/opt/venv` in the image (see [Dockerfile](Dockerfile)). If you add a dependency, update the `pip install` line there.
- The container starts the web.py app and Apache in one process tree (see [start-webpy.sh](start-webpy.sh)). The web.py server binds to `127.0.0.1:9000` (internal only).
- Files are edited on Windows but run in Linux containers; keep scripts **LF**.
  - The image normalizes CRLF for `cgi-bin/*.py`, `start-webpy.sh`, and `webapp/app.py` via `sed -i 's/\r$//'`.
  - If you add new executable scripts (new paths/extensions), extend those `sed/chmod` steps accordingly.

## Where to make changes
- Add/modify web routes: edit `urls` and handler classes in [webapp/app.py](webapp/app.py).
- Add CGI endpoints: add scripts under [cgi-bin/](cgi-bin/) and ensure they print a correct `Content-Type` header.
- Change proxy behavior (e.g., exempt additional paths from proxy): update [conf/extra/webpy-proxy.conf](conf/extra/webpy-proxy.conf).
