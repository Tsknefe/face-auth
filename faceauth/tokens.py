"""Step-up tokens: short-lived RS256 JWTs. Projects verify them with the PUBLIC key
(GET /.well-known/jwks.json) -- no shared secret between face-auth and your projects."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class TokenIssuer:
    def __init__(self, data_dir: Path, issuer: str, ttl_s: int):
        self.issuer, self.ttl = issuer, ttl_s
        key_path = data_dir / "signing_key.pem"
        if not key_path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                   serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
        self.private_pem = key_path.read_bytes()
        key = serialization.load_pem_private_key(self.private_pem, None)
        self.public_key = key.public_key()
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.public_key))
        self.kid = hashlib.sha256(self.public_pem()).hexdigest()[:16]   # stable key id
        jwk.update(kid=self.kid, use="sig", alg="RS256")
        self.jwk = jwk

    def public_pem(self) -> bytes:
        return self.public_key.public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)

    def issue(self, user_id: str, project: str, scope: str, similarity: float) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer, "sub": user_id, "aud": project, "scope": scope,
            "amr": ["face", "liveness"], "sim": round(similarity, 3),
            "iat": now, "exp": now + self.ttl, "jti": secrets.token_urlsafe(16),
        }
        return jwt.encode(claims, self.private_pem, algorithm="RS256", headers={"kid": self.kid})
