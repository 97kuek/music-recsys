"""Cloudflare Access JWTの検証。メール・JWTを研究ログへ保存しない。"""

import hashlib
import re

import jwt


class AccessVerifier:
    def __init__(self, team, audience):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", team) or not audience.strip():
            raise ValueError("Access team and audience must be configured")
        self.issuer = f"https://{team}.cloudflareaccess.com"
        self.audience = audience
        self.keys = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", timeout=5)

    def identity(self, token):
        if not token or len(token) > 16384:
            return None
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError):
            return None
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject or len(subject) > 1024:
            return None
        return "access:" + hashlib.sha256(f"{self.issuer}\n{subject}".encode()).hexdigest()

    def verify(self, token):
        return self.identity(token) is not None
