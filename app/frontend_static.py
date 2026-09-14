import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

INDEX_CACHE_CONTROL = "no-store"
# Theme ids are slugs; anything else does not reach an HTML attribute.
_THEME_ID_PATTERN = re.compile(r"[a-z0-9-]{1,40}")
# "Follow the operating system" is a choice the server cannot resolve — it depends
# on the device asking. It injects no attribute, which is what lets the
# prefers-color-scheme rules answer, since those are guarded on :not([data-theme]).
_FOLLOW_OS_THEME_ID = "follow-os"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
STATIC_FILE_CACHE_CONTROL = "public, max-age=3600"
FRONTEND_BUILD_INSTRUCTIONS = (
    "Run 'cd frontend && npm install && npm run build', "
    "or use a release zip that includes frontend/prebuilt."
)
_FORWARDED_HOST_RE = re.compile(
    r"^("
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"|\[(?:[0-9A-Fa-f:]+)\]"
    r"|(?:\d{1,3}(?:\.\d{1,3}){3})"
    r")(?::\d{1,5})?$"
)


def _sanitize_forwarded_proto(raw: str) -> str | None:
    proto = raw.split(",")[0].strip().lower()
    if proto in {"http", "https"}:
        return proto
    return None


def _sanitize_forwarded_host(raw: str) -> str | None:
    host = raw.split(",")[0].strip()
    if not host or any(char in host for char in "@/?#\\") or "://" in host:
        return None
    if not _FORWARDED_HOST_RE.fullmatch(host):
        return None
    return host


def _sanitize_forwarded_prefix(raw: str) -> str:
    prefix = raw.strip()
    if not prefix:
        return ""
    if not prefix.startswith("/") or any(char in prefix for char in "@?#\\") or "://" in prefix:
        return ""
    if ".." in prefix:
        return ""
    return prefix.rstrip("/")


class CacheControlStaticFiles(StaticFiles):
    """StaticFiles variant that adds a fixed Cache-Control header."""

    def __init__(self, *args, cache_control: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cache_control = cache_control

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = self.cache_control
        return response


async def _index_html_with_theme(index_file: Path) -> str:
    """The SPA shell with the stored theme already on the root element.

    The theme decides the page's background, so a page that learns it from a fetch
    paints the default first and then changes — a flash on every load, on every
    device, including the first visit where no local copy could have helped. The
    server knows the answer when it serves the page, so it says so in the page.

    Nothing caches this: the service worker has no fetch handler and the shell is
    sent `no-store`, so the injected value can never be a stale one.

    An empty theme means "follow the operating system"; nothing is injected and the
    `prefers-color-scheme` block in themes.css answers before any script runs.
    """
    html = index_file.read_text(encoding="utf-8")
    try:
        from app.repository import AppSettingsRepository

        theme = (await AppSettingsRepository.get()).ui_preferences.theme.strip()
    except Exception:
        # A settings read must never be what stops the app from being served.
        logger.exception("Could not read the stored theme; serving the shell unstyled")
        return html

    # `data-theme-server` says the server answered, which is not the same as it
    # having a theme: following the operating system is an answer and injects no
    # theme at all. Without the marker the client cannot tell that case from being
    # served statically, and would override the server with its own cached value.
    marker = "data-theme-server"

    # Ids come from a fixed list in the frontend, but this value ends up inside an
    # HTML attribute, so it is checked here rather than trusted.
    if theme == _FOLLOW_OS_THEME_ID or not theme or not _THEME_ID_PATTERN.fullmatch(theme):
        return html.replace("<html ", f"<html {marker} ", 1)
    return html.replace("<html ", f'<html data-theme="{theme}" {marker} ', 1)


def _file_response(path: Path, *, cache_control: str) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": cache_control})


def _is_index_file(path: Path, index_file: Path) -> bool:
    """Return True when the requested file is the SPA shell index.html."""
    return path == index_file


def _resolve_request_base(request: Request) -> str:
    """Resolve the external base URL, honoring common reverse-proxy headers.

    Returns a URL like ``https://host:8000/meshcore/`` (always trailing-slash)
    so callers can append paths directly.

    Recognized headers:
    - ``X-Forwarded-Proto`` + ``X-Forwarded-Host``: override scheme and host.
    - ``X-Forwarded-Prefix`` (or ``X-Forwarded-Path``): sub-path prefix added
      by the proxy (e.g. ``/meshcore``).
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    origin = str(request.base_url).rstrip("/")

    if forwarded_proto and forwarded_host:
        proto = _sanitize_forwarded_proto(forwarded_proto)
        host = _sanitize_forwarded_host(forwarded_host)
        if proto and host:
            origin = f"{proto}://{host}"

    # Sub-path prefix (e.g. /meshcore) communicated by the reverse proxy
    prefix = _sanitize_forwarded_prefix(
        request.headers.get("x-forwarded-prefix") or request.headers.get("x-forwarded-path") or ""
    )

    return f"{origin}{prefix}/"


def _validate_frontend_dir(frontend_dir: Path, *, log_failures: bool = True) -> tuple[bool, Path]:
    """Resolve and validate a built frontend directory."""
    frontend_dir = frontend_dir.resolve()
    index_file = frontend_dir / "index.html"

    if not frontend_dir.exists():
        if log_failures:
            logger.error("Frontend build directory not found at %s.", frontend_dir)
        return False, frontend_dir

    if not frontend_dir.is_dir():
        if log_failures:
            logger.error("Frontend build path is not a directory: %s.", frontend_dir)
        return False, frontend_dir

    if not index_file.exists():
        if log_failures:
            logger.error("Frontend index file not found at %s.", index_file)
        return False, frontend_dir

    return True, frontend_dir


def register_frontend_static_routes(app: FastAPI, frontend_dir: Path) -> bool:
    """Register frontend static file routes if a built frontend is available."""
    valid, frontend_dir = _validate_frontend_dir(frontend_dir)
    if not valid:
        return False

    index_file = frontend_dir / "index.html"
    assets_dir = frontend_dir / "assets"

    if assets_dir.exists() and assets_dir.is_dir():
        app.mount(
            "/assets",
            CacheControlStaticFiles(directory=assets_dir, cache_control=ASSET_CACHE_CONTROL),
            name="assets",
        )
    else:
        logger.warning(
            "Frontend assets directory missing at %s; /assets files will not be served",
            assets_dir,
        )

    @app.get("/")
    async def serve_index():
        """Serve the frontend index.html, already carrying the stored theme."""
        return HTMLResponse(
            await _index_html_with_theme(index_file),
            headers={"Cache-Control": INDEX_CACHE_CONTROL},
        )

    @app.get("/site.webmanifest")
    async def serve_webmanifest():
        """Serve the web manifest with same-origin relative URLs."""
        asset = "./"
        manifest = {
            "name": "Meshloom",
            "short_name": "Meshloom",
            "id": asset,
            "start_url": asset,
            "scope": asset,
            "display": "standalone",
            "display_override": ["window-controls-overlay", "standalone", "fullscreen"],
            "theme_color": "#0A0C10",
            "background_color": "#0A0C10",
            # Icons are PNG-only on purpose. iOS Safari's manifest parser has
            # historically been unreliable with SVG icons, and Android/Chrome
            # PWA install flows prefer PNG for the install prompt.
            #
            # The "any" purpose entries are what iOS and desktop Chrome use
            # for the home-screen / install icon. "maskable" entries are
            # Android-only (adaptive icon with safe-zone crop); iOS does not
            # apply the safe-zone mask, so a maskable-only icon set would
            # render with excessive padding.
            "icons": [
                {
                    "src": f"{asset}favicon-96x96.png",
                    "sizes": "96x96",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": f"{asset}apple-touch-icon.png",
                    "sizes": "180x180",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": f"{asset}favicon-256x256.png",
                    "sizes": "256x256",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": f"{asset}web-app-manifest-192x192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "maskable",
                },
                {
                    "src": f"{asset}web-app-manifest-512x512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
            "screenshots": [
                {
                    "src": f"{asset}screenshot-wide.png",
                    "sizes": "1367x909",
                    "type": "image/png",
                    "form_factor": "wide",
                    "label": "Meshloom desktop view",
                },
                {
                    "src": f"{asset}screenshot-mobile.png",
                    "sizes": "1170x2532",
                    "type": "image/png",
                    "label": "Meshloom mobile view",
                },
                {
                    "src": f"{asset}screenshot-mobile-2.png",
                    "sizes": "750x1334",
                    "type": "image/png",
                    "label": "Meshloom mobile conversation",
                },
            ],
        }
        return JSONResponse(
            manifest,
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve frontend files, falling back to index.html for SPA routing."""
        if path == "api" or path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={
                    "detail": (
                        "API endpoint not found. If you are seeing this in response to a "
                        "frontend request, you may be running a newer frontend with an older "
                        "backend or vice versa. A full update is suggested."
                    )
                },
            )

        file_path = (frontend_dir / path).resolve()
        try:
            file_path.relative_to(frontend_dir)
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found") from None

        if file_path.exists() and file_path.is_file():
            cache_control = (
                INDEX_CACHE_CONTROL
                if _is_index_file(file_path, index_file)
                else STATIC_FILE_CACHE_CONTROL
            )
            return _file_response(file_path, cache_control=cache_control)

        return _file_response(index_file, cache_control=INDEX_CACHE_CONTROL)

    logger.info("Serving frontend from %s", frontend_dir)
    return True


def register_first_available_frontend_static_routes(
    app: FastAPI, frontend_dirs: list[Path]
) -> Path | None:
    """Register frontend routes from the first valid build directory."""
    for i, candidate in enumerate(frontend_dirs):
        valid, resolved_candidate = _validate_frontend_dir(candidate, log_failures=False)
        if not valid:
            continue

        if register_frontend_static_routes(app, resolved_candidate):
            logger.info("Selected frontend build directory %s", resolved_candidate)
            return resolved_candidate

        if i < len(frontend_dirs) - 1:
            logger.warning("Frontend build at %s was unusable; trying fallback", resolved_candidate)

    logger.error(
        "No usable frontend build found. Searched: %s. %s API will continue without frontend routes.",
        ", ".join(str(path.resolve()) for path in frontend_dirs),
        FRONTEND_BUILD_INSTRUCTIONS,
    )
    return None


def register_frontend_missing_fallback(app: FastAPI) -> None:
    """Register a fallback route that tells the user to build the frontend."""

    @app.get("/", include_in_schema=False)
    async def frontend_not_built():
        return JSONResponse(
            status_code=404,
            content={"detail": f"Frontend not built. {FRONTEND_BUILD_INSTRUCTIONS}"},
        )
