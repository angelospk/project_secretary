"""The console ASGI app: public read views + admin-gated writes (Starlette + Jinja).

Connection-per-request (the existing `surreal` context manager) keeps failures isolated
and sidesteps client thread-safety questions — fine for a read-mostly tool on a small VM.
Admin is hard-disabled when no password hash is configured: the login route and every
mutation 404, so a public deploy with no secret simply cannot write. Mutations require
both an admin session and a matching per-session CSRF token.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from secretary.config import Settings
from secretary.console import auth, data
from secretary.console import state as console_state
from secretary.db.connection import surreal

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


class _SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


def build_app(settings: Settings) -> Starlette:
    repo = settings.repo_list[0]

    def _ctx(request: Request, **extra) -> dict:
        return {
            "request": request,
            "repo": repo,
            "admin_enabled": auth.admin_enabled(settings),
            "is_admin": auth.is_admin(request.session),
            "csrf": auth.issue_csrf(request.session) if auth.is_admin(request.session) else "",
            **extra,
        }

    def _require_admin(request: Request) -> None:
        if not auth.admin_enabled(settings):
            raise HTTPException(404)  # viewer-only mode: no admin surface at all
        if not auth.is_admin(request.session):
            raise HTTPException(403)

    async def _require_admin_post(request: Request) -> dict:
        _require_admin(request)
        form = await request.form()
        if not auth.check_csrf(request.session, form.get("csrf")):
            raise HTTPException(403)
        return form

    # --- public read views ----------------------------------------------------

    async def index(request: Request) -> Response:
        with surreal(settings) as db:
            ctx = _ctx(request,
                       status=data.status(db, settings, repo),
                       activity=data.weekly_activity(db, repo),
                       next_release=console_state.get_next_release(db, repo),
                       digest=console_state.effective_digest(
                           settings, console_state.get_digest_overrides(db, repo)))
        return _TEMPLATES.TemplateResponse(request, "index.html", ctx)

    async def clusters(request: Request) -> Response:
        with surreal(settings) as db:
            ctx = _ctx(request, clusters=data.clusters(db, settings, repo))
        return _TEMPLATES.TemplateResponse(request, "clusters.html", ctx)

    async def release(request: Request) -> Response:
        milestone = request.path_params["milestone"]
        with surreal(settings) as db:
            ctx = _ctx(request, progress=data.release_progress(db, repo, milestone))
        return _TEMPLATES.TemplateResponse(request, "release.html", ctx)

    # --- auth -----------------------------------------------------------------

    async def login_form(request: Request) -> Response:
        if not auth.admin_enabled(settings):
            raise HTTPException(404)
        return _TEMPLATES.TemplateResponse(request, "login.html", _ctx(request, error=None))

    async def login(request: Request) -> Response:
        if not auth.admin_enabled(settings):
            raise HTTPException(404)
        form = await request.form()
        if auth.check_login(settings, str(form.get("password", ""))):
            request.session.clear()  # rotate session on privilege change
            request.session["admin"] = True
            return RedirectResponse("/admin", status_code=303)
        return _TEMPLATES.TemplateResponse(
            request, "login.html", _ctx(request, error="incorrect password"), status_code=401)

    async def logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    # --- admin panel + writes -------------------------------------------------

    async def admin(request: Request) -> Response:
        _require_admin(request)
        with surreal(settings) as db:
            ctx = _ctx(request,
                       clusters=data.clusters(db, settings, repo),
                       next_release=console_state.get_next_release(db, repo),
                       digest=console_state.effective_digest(
                           settings, console_state.get_digest_overrides(db, repo)),
                       saved=request.query_params.get("saved"))
        return _TEMPLATES.TemplateResponse(request, "admin.html", ctx)

    async def rename_label(request: Request) -> Response:
        form = await _require_admin_post(request)
        try:
            with surreal(settings) as db:
                console_state.set_label(db, repo, str(form.get("key", "")), str(form.get("name", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return RedirectResponse("/admin?saved=label", status_code=303)

    async def set_next_release(request: Request) -> Response:
        form = await _require_admin_post(request)
        categories = [c.strip() for c in str(form.get("categories", "")).split(",") if c.strip()]
        try:
            items = _parse_items(str(form.get("items", "")))
            with surreal(settings) as db:
                console_state.set_next_release(db, repo, categories, items)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return RedirectResponse("/admin?saved=release", status_code=303)

    async def set_digest(request: Request) -> Response:
        form = await _require_admin_post(request)
        enabled = str(form.get("enabled", "")).lower() in ("1", "true", "on", "yes")
        try:
            interval = int(form.get("interval_days", 7))
            with surreal(settings) as db:
                console_state.set_digest(db, repo, enabled, interval)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        return RedirectResponse("/admin?saved=digest", status_code=303)

    routes = [
        Route("/", index),
        Route("/clusters", clusters),
        Route("/releases/{milestone:path}", release),
        Route("/login", login_form, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/admin", admin),
        Route("/admin/label", rename_label, methods=["POST"]),
        Route("/admin/next-release", set_next_release, methods=["POST"]),
        Route("/admin/digest", set_digest, methods=["POST"]),
    ]

    middleware = [
        Middleware(_SecurityHeaders),
        Middleware(
            SessionMiddleware,
            secret_key=settings.console_session_secret or "insecure-dev-only",
            session_cookie="secretary_console",
            same_site="strict",
            https_only=settings.console_https,
        ),
    ]

    return Starlette(routes=routes, middleware=middleware)


def _parse_items(raw: str) -> list[dict]:
    """Parse a textarea of `issue 12`/`pr 34` lines into [{kind, number}]."""
    items: list[dict] = []
    for line in raw.splitlines():
        parts = line.replace("#", " ").split()
        if len(parts) != 2:
            if line.strip():
                raise HTTPException(400, f"bad item line: {line!r} (expected 'issue 12')")
            continue
        kind, num = parts[0].strip().lower(), parts[1].strip()
        items.append({"kind": kind, "number": int(num) if num.isdigit() else 0})
    return items


def serve_console(settings: Settings) -> None:
    """Run the console with uvicorn until interrupted."""
    import uvicorn

    if settings.console_password.strip() and not settings.console_session_secret.strip():
        raise SystemExit(
            "SECRETARY_CONSOLE_PASSWORD is set but SECRETARY_CONSOLE_SESSION_SECRET is empty; "
            "admin sessions need a signing secret. Set one (any long random string)."
        )
    uvicorn.run(build_app(settings), host=settings.console_host, port=settings.console_port)
