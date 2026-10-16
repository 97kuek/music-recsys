"""Cloudflare Access JWTの検証。メール・JWTを研究ログへ保存しない。"""

import re

import jwt


class AccessVerifier:
    def __init__(self, team, audience):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", team) or not audience.strip():
            raise ValueError("Access team and audience must be configured")
        self.issuer = f"https://{team}.cloudflareaccess.com"
        self.audience = audience
        self.keys = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", timeout=5)

    def verify(self, token):
        if not token or len(token) > 16384:
            return False
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError):
            return False
        return True
