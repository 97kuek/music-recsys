"""仮名参加者の識別キー重複。"""


class IdentityCollisionError(ValueError):
    """同じ認証IDの同時登録。"""
