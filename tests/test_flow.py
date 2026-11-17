"""End-to-end tests with a fake face engine (no camera / models needed).

A fake "frame" is JSON: {"person": "alice", "yaw": 0.3, "pitch": 0, "faces": 1, "size": 200}
"""
import base64
import json
import time

import jwt
import numpy as np
import pytest

from faceauth.config import Settings
from faceauth.engine import FaceObs
from faceauth.service import AuthError, FaceAuthService

POSE = {"center": (0.0, 0.0), "turn_left": (0.35, 0.0), "turn_right": (-0.35, 0.0),
        "look_up": (0.0, -0.2), "look_down": (0.0, 0.2)}


class FakeEngine:
    def analyze(self, b: bytes):
        d = json.loads(b)
        if d.get("faces", 1) == 0:
            return None
        rng = np.random.default_rng(abs(hash(d["person"])) % 2**32)
        base = rng.normal(size=128).astype(np.float32)
        noise = np.random.default_rng().normal(size=128).astype(np.float32) * 0.15
        emb = base + noise
        emb /= np.linalg.norm(emb)
        yaw, pitch = d.get("yaw", 0.0), d.get("pitch", 0.0)
        lm = np.array([[70, 100], [130, 100],
                       [100 + yaw * 60, 100 + (0.55 + pitch) * 60],
                       [80, 160], [120, 160]], np.float32)
        s = d.get("size", 200)
        return FaceObs(bbox=(0, 0, s, s), landmarks=lm, embedding=emb, score=0.99,
                       real_score=d.get("real"), n_faces=d.get("faces", 1))


def frames_for(actions, person="alice", **over):
    out = []
    for a in actions:
        y, p = POSE[a]
        d = {"person": person, "yaw": y, "pitch": p}
        d.update(over)
        out.append(json.dumps(d).encode())
    return out


@pytest.fixture
def svc(tmp_path):
    cfg = Settings(data_dir=tmp_path, model_dir=tmp_path, api_keys_raw="crypto:K1,blog:K2")
    return FaceAuthService(cfg, FakeEngine())


def enroll(svc, user="u1", person="alice", project="crypto"):
    ch = svc.start(project, user, "enroll")
    return svc.complete(ch["challenge_id"], frames_for(ch["actions"], person))


def test_enroll_then_verify_issues_valid_token(svc):
    assert enroll(svc)["enrolled"]
    ch = svc.start("crypto", "u1", "verify", scope="withdraw")
    assert ch["actions"][0] == "center" and len(ch["actions"]) == 3
    res = svc.complete(ch["challenge_id"], frames_for(ch["actions"]))
    claims = jwt.decode(res["token"], svc.tokens.public_key, algorithms=["RS256"],
                        audience="crypto", issuer="face-auth")
    assert claims["sub"] == "u1" and claims["scope"] == "withdraw"
    assert claims["exp"] - claims["iat"] == svc.cfg.token_ttl_s


def test_other_person_is_rejected(svc):
    enroll(svc)
    ch = svc.start("crypto", "u1", "verify")
    with pytest.raises(AuthError) as e:
        svc.complete(ch["challenge_id"], frames_for(ch["actions"], person="mallory"))
    assert e.value.code == "no_match"


def test_static_photo_fails_liveness(svc):
    enroll(svc)
    ch = svc.start("crypto", "u1", "verify")
    still = frames_for(["center"] * len(ch["actions"]))     # same pose every frame
    with pytest.raises(AuthError) as e:
        svc.complete(ch["challenge_id"], still)
    assert e.value.code == "liveness_failed"


def test_wrong_order_fails(svc):
    enroll(svc)
    ch = svc.start("crypto", "u1", "verify")
    acts = ["center"] + list(reversed(ch["actions"][1:]))
    if acts == ch["actions"]:
        pytest.skip("reversal identical")
    with pytest.raises(AuthError):
        svc.complete(ch["challenge_id"], frames_for(acts))


def test_face_swap_between_frames_fails(svc):
    enroll(svc)
    ch = svc.start("crypto", "u1", "verify")
    fr = frames_for(ch["actions"])
    fr[-1] = frames_for([ch["actions"][-1]], person="mallory")[0]
    with pytest.raises(AuthError) as e:
        svc.complete(ch["challenge_id"], fr)
    assert "changed" in e.value.message


def test_two_faces_and_small_face_and_spoof(svc):
    enroll(svc)
    for over, word in [({"faces": 2}, "one face"), ({"size": 40}, "too small"), ({"real": 0.1}, "attack")]:
        ch = svc.start("crypto", "u1", "verify")
        with pytest.raises(AuthError) as e:
            svc.complete(ch["challenge_id"], frames_for(ch["actions"], **over))
        assert word in e.value.message


def test_challenge_is_one_time(svc):
    enroll(svc)
    ch = svc.start("crypto", "u1", "verify")
    fr = frames_for(ch["actions"])
    svc.complete(ch["challenge_id"], fr)
    with pytest.raises(AuthError) as e:
        svc.complete(ch["challenge_id"], fr)
    assert e.value.code == "bad_challenge"


def test_challenge_expires(svc):
    enroll(svc)
    ch = svc.start("crypto", "u1", "verify")
    svc._challenges[ch["challenge_id"]].created -= svc.cfg.challenge_ttl_s + 1
    with pytest.raises(AuthError):
        svc.complete(ch["challenge_id"], frames_for(ch["actions"]))


def test_lockout_after_failures(svc):
    enroll(svc)
    for _ in range(svc.cfg.max_failures):
        ch = svc.start("crypto", "u1", "verify")
        with pytest.raises(AuthError):
            svc.complete(ch["challenge_id"], frames_for(ch["actions"], person="mallory"))
    with pytest.raises(AuthError) as e:
        svc.start("crypto", "u1", "verify")
    assert e.value.code == "locked"


def test_verify_requires_enrollment_and_delete_works(svc):
    with pytest.raises(AuthError) as e:
        svc.start("crypto", "nobody", "verify")
    assert e.value.code == "not_enrolled"
    enroll(svc)
    assert svc.delete_user("crypto", "u1")
    with pytest.raises(AuthError):
        svc.start("crypto", "u1", "verify")


def test_templates_are_encrypted_at_rest(svc):
    enroll(svc)
    raw = svc.store.db.execute("SELECT emb FROM templates").fetchone()[0]
    emb = svc.store.get("u1")
    assert emb.tobytes() not in raw and len(emb) == 128


def test_token_audience_is_project(svc):
    enroll(svc)
    ch = svc.start("blog", "u1", "verify")
    tok = svc.complete(ch["challenge_id"], frames_for(ch["actions"]))["token"]
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(tok, svc.tokens.public_key, algorithms=["RS256"], audience="crypto")


# ------------------------------------------------------------------ HTTP layer
def test_http_api(svc):
    from starlette.testclient import TestClient
    from faceauth.api import create_app
    c = TestClient(create_app(service=svc))

    assert c.post("/v1/challenges", json={"user_id": "u1", "purpose": "enroll"}).status_code == 401
    H = {"X-API-Key": "K1"}
    ch = c.post("/v1/challenges", json={"user_id": "u1", "purpose": "enroll"}, headers=H).json()
    b64 = [base64.b64encode(f).decode() for f in frames_for(ch["actions"])]
    assert c.post(f"/v1/challenges/{ch['challenge_id']}/complete", json={"frames": b64}).json()["enrolled"]

    ch = c.post("/v1/challenges", json={"user_id": "u1", "scope": "withdraw"}, headers=H).json()
    b64 = ["data:image/jpeg;base64," + base64.b64encode(f).decode() for f in frames_for(ch["actions"])]
    r = c.post(f"/v1/challenges/{ch['challenge_id']}/complete", json={"frames": b64})
    assert r.status_code == 200
    tok = r.json()["token"]

    jwks = c.get("/.well-known/jwks.json").json()
    key = jwt.PyJWK(jwks["keys"][0]).key
    assert jwt.decode(tok, key, algorithms=["RS256"], audience="crypto")["scope"] == "withdraw"

    bad = c.post("/v1/challenges/nope/complete", json={"frames": b64})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_challenge"
    assert c.post("/v1/challenges/x/complete", json={"frames": "x"}).status_code == 400
    assert c.delete("/v1/users/u1", headers=H).json()["deleted"]
    assert c.get("/faceauth.js").status_code == 200


def test_python_client_verify_token(svc, monkeypatch):
    import sys, types
    sys.path.insert(0, "clients/python")
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(Client=lambda **k: None))
    from faceauth_client import FaceAuthClient, FaceAuthError

    enroll(svc)
    ch = svc.start("crypto", "u1", "verify", scope="withdraw")
    tok = svc.complete(ch["challenge_id"], frames_for(ch["actions"]))["token"]

    fa = FaceAuthClient("http://x", api_key="K1", project="crypto")
    fa._jwks = types.SimpleNamespace(
        get_signing_key_from_jwt=lambda t: types.SimpleNamespace(key=svc.tokens.public_key))
    with pytest.raises(FaceAuthError, match="another user"):
        fa.verify_token(tok, user_id="u2", scope="withdraw")
    with pytest.raises(FaceAuthError, match="scope"):
        fa.verify_token(tok, user_id="u1", scope="trade")
    assert fa.verify_token(tok, user_id="u1", scope="withdraw")["sub"] == "u1"
    with pytest.raises(FaceAuthError, match="already used"):
        fa.verify_token(tok, user_id="u1", scope="withdraw")
    other = FaceAuthClient("http://x", api_key="K2", project="blog")
    other._jwks = fa._jwks
    with pytest.raises(FaceAuthError, match="invalid"):
        other.verify_token(tok, user_id="u1")
