"""Core logic, independent of the web framework (easy to test)."""
from __future__ import annotations

import threading
import time
from typing import Optional

from .config import Settings
from .engine import Engine, cosine
from .liveness import ACTION_TEXT_TR, Challenge, check, new_challenge
from .store import Store
from .tokens import TokenIssuer


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class FaceAuthService:
    def __init__(self, cfg: Settings, engine: Engine):
        self.cfg = cfg
        self.engine = engine
        self.store = Store(cfg.data_dir)
        self.tokens = TokenIssuer(cfg.data_dir, cfg.issuer, cfg.token_ttl_s)
        self._challenges: dict[str, Challenge] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ challenges
    def start(self, project: str, user_id: str, purpose: str, scope: str = "") -> dict:
        if purpose not in ("enroll", "verify"):
            raise AuthError("bad_purpose", "purpose must be 'enroll' or 'verify'")
        if not user_id or len(user_id) > 128:
            raise AuthError("bad_user", "invalid user_id")
        wait = self.store.locked_for(user_id, self.cfg.max_failures, self.cfg.lockout_s)
        if wait > 0:
            raise AuthError("locked", f"too many failed attempts, retry in {int(wait)} s", 429)
        if purpose == "verify" and self.store.get(user_id) is None:
            raise AuthError("not_enrolled", "user has no face enrolled", 404)
        ch = new_challenge(user_id, purpose, scope, project, self.cfg.challenge_actions)
        with self._lock:
            self._gc()
            self._challenges[ch.id] = ch
        return {"challenge_id": ch.id, "actions": ch.actions,
                "instructions": [ACTION_TEXT_TR[a] for a in ch.actions],
                "expires_in": self.cfg.challenge_ttl_s}

    def _gc(self):
        now = time.time()
        for k in [k for k, c in self._challenges.items() if now - c.created > self.cfg.challenge_ttl_s]:
            del self._challenges[k]

    def _take(self, challenge_id: str) -> Challenge:
        with self._lock:
            ch = self._challenges.pop(challenge_id, None)     # one-time use (anti-replay)
        if ch is None or time.time() - ch.created > self.cfg.challenge_ttl_s:
            raise AuthError("bad_challenge", "challenge unknown, used or expired", 400)
        return ch

    # ------------------------------------------------------------------ complete
    def complete(self, challenge_id: str, frames: list[bytes]) -> dict:
        """Called by the browser. The challenge id is an unguessable one-time capability
        created by the project's backend, so no API key is needed (or exposed) here."""
        ch = self._take(challenge_id)
        project = ch.project
        try:
            obs = [self.engine.analyze(f) for f in frames]
        except ValueError as e:
            raise AuthError("bad_image", str(e))
        live = check(ch, obs, self.cfg)
        if not live.ok:
            self._failed(ch, "liveness", live.reason)
            raise AuthError("liveness_failed", live.reason, 401)

        if ch.purpose == "enroll":
            self.store.put(ch.user_id, live.embedding)
            self.store.reset_failures(ch.user_id)
            self.store.audit(project, ch.user_id, "enroll", True)
            return {"enrolled": True, "user_id": ch.user_id}

        ref = self.store.get(ch.user_id)
        if ref is None:
            raise AuthError("not_enrolled", "user has no face enrolled", 404)
        sim = cosine(ref, live.embedding)
        if sim < self.cfg.match_threshold:
            self._failed(ch, "match", f"similarity {sim:.3f}")
            raise AuthError("no_match", "face does not match the enrolled user", 401)
        self.store.reset_failures(ch.user_id)
        self.store.audit(project, ch.user_id, "verify", True, {"scope": ch.scope, "sim": round(sim, 3)})
        token = self.tokens.issue(ch.user_id, project, ch.scope, sim)
        return {"verified": True, "token": token, "expires_in": self.cfg.token_ttl_s}

    def _failed(self, ch: Challenge, stage: str, reason: str):
        self.store.fail(ch.user_id, self.cfg.lockout_s)
        self.store.audit(ch.project, ch.user_id, ch.purpose, False, {"stage": stage, "reason": reason})

    # ------------------------------------------------------------------ misc
    def delete_user(self, project: str, user_id: str) -> bool:
        ok = self.store.delete(user_id)
        self.store.audit(project, user_id, "delete", ok)
        return ok

    def jwks(self) -> dict:
        return {"keys": [self.tokens.jwk]}


def build_default(cfg: Optional[Settings] = None) -> FaceAuthService:
    from .engine import OpenCVEngine
    cfg = cfg or Settings()
    return FaceAuthService(cfg, OpenCVEngine(cfg.model_dir, cfg.antispoof_model))
