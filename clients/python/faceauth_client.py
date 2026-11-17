"""Drop-in helper for YOUR projects' backends (single file, copy it anywhere).

    pip install pyjwt[crypto] httpx

    from faceauth_client import FaceAuthClient
    fa = FaceAuthClient("http://localhost:8100", api_key="KEY1", project="crypto")

    # 1) when the user asks for something critical, create a challenge for the browser
    ch = fa.create_challenge(user_id=str(user.id), scope="withdraw")      # -> dict for the widget

    # 2) when the critical request arrives with the token from the widget
    fa.verify_token(token, user_id=str(user.id), scope="withdraw")        # raises on failure

Framework helpers at the bottom: FastAPI dependency and Flask decorator.
"""
from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Optional

import jwt


class FaceAuthError(Exception):
    pass


class FaceAuthClient:
    def __init__(self, base_url: str, api_key: str, project: str,
                 issuer: str = "face-auth", timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.api_key, self.project, self.issuer = api_key, project, issuer
        import httpx
        self.http = httpx.Client(timeout=timeout)
        self._jwks: Optional[jwt.PyJWKClient] = None
        self._used_jti: dict[str, float] = {}          # one-time tokens
        self._lock = threading.Lock()

    # -- calls to the face-auth service (server side, uses the API key) -------------
    def _post(self, path: str, payload: dict) -> dict:
        r = self.http.post(self.base + path, json=payload, headers={"X-API-Key": self.api_key})
        if r.status_code >= 400:
            raise FaceAuthError(r.json().get("message", r.text))
        return r.json()

    def create_challenge(self, user_id: str, scope: str = "", purpose: str = "verify") -> dict:
        """Returns {challenge_id, actions, instructions, expires_in} -> give it to the widget."""
        return self._post("/v1/challenges", {"user_id": user_id, "scope": scope, "purpose": purpose})

    def create_enrollment(self, user_id: str) -> dict:
        return self.create_challenge(user_id, purpose="enroll")

    def delete_user(self, user_id: str) -> bool:
        r = self.http.delete(f"{self.base}/v1/users/{user_id}", headers={"X-API-Key": self.api_key})
        return r.status_code < 400 and r.json().get("deleted", False)

    # -- token verification (offline, with the public key) --------------------------
    def verify_token(self, token: str, user_id: str, scope: str = "") -> dict:
        if self._jwks is None:
            self._jwks = jwt.PyJWKClient(self.base + "/.well-known/jwks.json", cache_keys=True)
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.project,
                                issuer=self.issuer, options={"require": ["exp", "sub", "jti"]})
        except jwt.PyJWTError as e:
            raise FaceAuthError(f"invalid face token: {e}")
        if claims["sub"] != str(user_id):
            raise FaceAuthError("face token belongs to another user")
        if scope and claims.get("scope") != scope:
            raise FaceAuthError(f"face token is for scope '{claims.get('scope')}', not '{scope}'")
        now = time.time()
        with self._lock:
            for j, exp in list(self._used_jti.items()):
                if exp < now:
                    del self._used_jti[j]
            if claims["jti"] in self._used_jti:
                raise FaceAuthError("face token already used")
            self._used_jti[claims["jti"]] = claims["exp"]
        return claims


# ------------------------------------------------------------------ FastAPI
def fastapi_dependency(client: FaceAuthClient, scope: str, get_user_id):
    """
    require_face = fastapi_dependency(fa, "withdraw", get_user_id=lambda req: req.state.user_id)
    @app.post("/withdraw")
    def withdraw(..., face=Depends(require_face)): ...
    Token is read from the 'X-Face-Token' header.
    """
    from fastapi import HTTPException, Request

    def dep(request: Request):
        tok = request.headers.get("x-face-token")
        if not tok:
            raise HTTPException(401, "face verification required", headers={"X-Face-Required": scope})
        try:
            return client.verify_token(tok, str(get_user_id(request)), scope)
        except FaceAuthError as e:
            raise HTTPException(401, str(e), headers={"X-Face-Required": scope})
    return dep


# ------------------------------------------------------------------ Flask
def flask_require_face(client: FaceAuthClient, scope: str, get_user_id):
    """
    @app.post("/withdraw")
    @flask_require_face(fa, "withdraw", get_user_id=lambda: current_user.id)
    def withdraw(): ...
    """
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            from flask import jsonify, request
            tok = request.headers.get("X-Face-Token")
            try:
                if not tok:
                    raise FaceAuthError("face verification required")
                client.verify_token(tok, str(get_user_id()), scope)
            except FaceAuthError as e:
                resp = jsonify(error="face_required", message=str(e), scope=scope)
                resp.status_code = 401
                resp.headers["X-Face-Required"] = scope
                return resp
            return fn(*a, **kw)
        return inner
    return deco
