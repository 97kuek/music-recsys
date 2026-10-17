"""仮名参加コードの発行・正規化・照合キー。"""

import hashlib
import re
import secrets


def code_key(code):
    normalized = re.sub(r"[\s-]", "", code).upper()
    if not re.fullmatch(r"[0-9A-F]{24}", normalized):
        raise PermissionError("参加コードを確認してください")
    return hashlib.sha256(normalized.encode()).hexdigest()


def new_code():
    value = secrets.token_hex(12).upper()
    return "-".join(value[i : i + 4] for i in range(0, len(value), 4))
