"""仮名参加コードの発行・正規化・照合キー。"""

import hashlib
import re
import secrets


class CodeCollisionError(ValueError):
    """参加コードが既存の参加者と重複している。"""


def code_key(code):
    normalized = re.sub(r"[\s-]", "", code).upper()
    if not re.fullmatch(r"(?:[0-9]{4}|[0-9A-F]{24})", normalized):
        raise PermissionError("参加コードを確認してください")
    return hashlib.sha256(normalized.encode()).hexdigest()


def new_code():
    return f"{secrets.randbelow(10000):04d}"
