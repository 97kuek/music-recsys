"""比較条件・順序割付・保存済み計画の識別。"""

import hashlib
import json
import random
from dataclasses import asdict, dataclass

ROUTES = frozenset(
    {
        "popularity",
        "itemcf_history",
        "trained_user",
        "als_fold_in_binary_seeds",
        "popularity_fallback",
    }
)


@dataclass(frozen=True)
class Condition:
    condition_id: str
    version: str
    offline_run_ref: str | None = None
    required_route: str | None = None

    def __post_init__(self):
        for value, limit in ((self.condition_id, 100), (self.version, 200)):
            if not isinstance(value, str) or not 1 <= len(value) <= limit:
                raise ValueError("invalid condition identifier")
        if self.required_route is not None and self.required_route not in ROUTES:
            raise ValueError("invalid required route")

    def model_dump(self):
        return asdict(self)


@dataclass(frozen=True)
class StudyPlan:
    protocol_id: str
    seed: int
    top_k: int
    conditions: tuple[Condition, ...]

    def __post_init__(self):
        if not isinstance(self.protocol_id, str) or not 1 <= len(self.protocol_id) <= 100:
            raise ValueError("invalid protocol identifier")
        if type(self.seed) is not int or type(self.top_k) is not int or not 2 <= self.top_k <= 20:
            raise ValueError("invalid study seed or top_k")
        if not 2 <= len(self.conditions) <= 8:
            raise ValueError("study requires 2 to 8 conditions")
        if len({c.condition_id for c in self.conditions}) != len(self.conditions):
            raise ValueError("duplicate condition")
        object.__setattr__(self, "conditions", tuple(self.conditions))

    def model_dump(self):
        return {
            "protocol_id": self.protocol_id,
            "seed": self.seed,
            "top_k": self.top_k,
            "conditions": [condition.model_dump() for condition in self.conditions],
        }

    @property
    def digest(self):
        # Keep the deployed v1 JSON encoding: existing participant assignments can resume.
        payload = json.dumps(self.model_dump(), ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def order(self, participant):
        order = list(range(len(self.conditions)))
        random.Random(f"{self.seed}:{participant}").shuffle(order)
        return order
