"""HTTP API (Starlette). Run:  uvicorn faceauth.api:app --port 8100

Flow
  1. Your project's BACKEND (with its API key):  POST /v1/challenges
        {"user_id": "42", "purpose": "verify", "scope": "withdraw"}
     -> {"challenge_id", "actions", "instructions", "expires_in"}
  2. The BROWSER (faceauth.js widget) records one frame per action and posts them:
        POST /v1/challenges/{id}/complete   {"frames": ["<base64 jpeg>", ...]}
     -> verify: {"verified": true, "token": "<JWT>"}   enroll: {"enrolled": true}
  3. The browser sends the token with the critical request; your backend checks it
     with the public key from GET /.well-known/jwks.json (see clients/python).
"""
from __future__ import annotations

import base64
import binascii
import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .config import Settings
from .service import AuthError, FaceAuthService, build_default

MAX_FRAMES = 8
MAX_FRAME_BYTES = 2_000_000


def create_app(service: FaceAuthService | None = None, cfg: Settings | None = None) -> Starlette:
    cfg = cfg or (service.cfg if service else Settings())
    svc = service or build_default(cfg)
    keys = cfg.api_keys
    if not keys:
        print("[face-auth] WARNING: no FACEAUTH_API_KEYS set -> dev mode, every caller is project 'dev'")

    def project_of(req: Request) -> str:
        if not keys:
            return "dev"
        p = keys.get(req.headers.get("x-api-key", ""))
        if p is None:
            raise AuthError("unauthorized", "missing or invalid X-API-Key", 401)
        return p

    async def body(req: Request) -> dict:
        try:
            d = await req.json()
        except Exception:
            raise AuthError("bad_json", "request body must be JSON")
        if not isinstance(d, dict):
            raise AuthError("bad_json", "request body must be a JSON object")
        return d

    async def create_challenge(req: Request):
        project = project_of(req)
        d = await body(req)
        return JSONResponse(svc.start(project, str(d.get("user_id", "")),
                                      str(d.get("purpose", "verify")), str(d.get("scope", ""))))

    async def complete(req: Request):
        d = await body(req)
        raw = d.get("frames")
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_FRAMES:
            raise AuthError("bad_frames", f"'frames' must be a list of 1..{MAX_FRAMES} images")
        frames = []
        for f in raw:
            if not isinstance(f, str):
                raise AuthError("bad_frames", "frames must be base64 strings")
            if f.startswith("data:"):
                f = f.split(",", 1)[-1]
            try:
                b = base64.b64decode(f, validate=True)
            except (binascii.Error, ValueError):
                raise AuthError("bad_frames", "invalid base64")
            if len(b) > MAX_FRAME_BYTES:
                raise AuthError("bad_frames", "frame too large")
            frames.append(b)
        return JSONResponse(svc.complete(req.path_params["cid"], frames))

    async def delete_user(req: Request):
        project = project_of(req)
        return JSONResponse({"deleted": svc.delete_user(project, req.path_params["uid"])})

    async def jwks(req: Request):
        return JSONResponse(svc.jwks())

    async def health(req: Request):
        return JSONResponse({"ok": True})

    static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

    async def widget(req: Request):
        return FileResponse(os.path.join(static, "faceauth.js"), media_type="application/javascript")

    async def demo(req: Request):
        return FileResponse(os.path.join(static, "demo.html"))

    async def on_auth_error(req: Request, exc: AuthError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    origins = [o.strip() for o in os.environ.get("FACEAUTH_CORS_ORIGINS", "*").split(",") if o.strip()]
    app = Starlette(
        routes=[
            Route("/v1/challenges", create_challenge, methods=["POST"]),
            Route("/v1/challenges/{cid}/complete", complete, methods=["POST"]),
            Route("/v1/users/{uid}", delete_user, methods=["DELETE"]),
            Route("/.well-known/jwks.json", jwks),
            Route("/health", health),
            Route("/faceauth.js", widget),
            Route("/demo", demo),
        ],
        middleware=[Middleware(CORSMiddleware, allow_origins=origins,
                               allow_methods=["GET", "POST", "DELETE"],
                               allow_headers=["content-type", "x-api-key"])],
        exception_handlers={AuthError: on_auth_error},
    )
    app.state.service = svc
    return app


def __getattr__(name):            # lazy: `uvicorn faceauth.api:app`
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
