"""比較条件・順序割付・保存済み計画の識別。"""

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from itertools import permutations

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
    controller: str | None = None

    def __post_init__(self):
        for value, limit in ((self.condition_id, 100), (self.version, 200)):
            if not isinstance(value, str) or not 1 <= len(value) <= limit:
                raise ValueError("invalid condition identifier")
        if self.required_route is not None and self.required_route not in ROUTES:
            raise ValueError("invalid required route")
        if self.controller not in {None, "fixed", "llm_single", "reactive_agent"}:
            raise ValueError("invalid controller")

    def model_dump(self):
        result = asdict(self)
        if self.controller is None:
            del result["controller"]
        return result


@dataclass(frozen=True)
class StudyPlan:
    protocol_id: str
    seed: int
    top_k: int
    conditions: tuple[Condition, ...]
    design: str | None = None

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
        if self.design not in {None, "exploratory_standard", "exploratory_strategy"}:
            raise ValueError("invalid exploratory design")
        if self.design and (
            self.top_k != 5
            or len(self.conditions) != (3 if self.design == "exploratory_standard" else 2)
        ):
            raise ValueError("exploratory design requires five tracks and three/two conditions")
        if self.design == "exploratory_standard" and (
            len({c.version for c in self.conditions}) != 3
            or any(c.controller not in {None, "fixed"} for c in self.conditions)
        ):
            raise ValueError("standard comparison requires three distinct registered versions")
        if self.design == "exploratory_strategy" and {c.controller for c in self.conditions} != {
            "llm_single",
            "reactive_agent",
        }:
            raise ValueError("strategy comparison requires llm_single and reactive_agent")

    def model_dump(self):
        result = {
            "protocol_id": self.protocol_id,
            "seed": self.seed,
            "top_k": self.top_k,
            "conditions": [condition.model_dump() for condition in self.conditions],
        }
        if self.design:
            result["design"] = self.design
        return result

    @property
    def digest(self):
        # Keep the deployed v1 JSON encoding: existing participant assignments can resume.
        payload = json.dumps(self.model_dump(), ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def order(self, participant):
        if self.design:
            raise ValueError("exploratory assignments require a persisted enrollment slot")
        order = list(range(len(self.conditions)))
        random.Random(f"{self.seed}:{participant}").shuffle(order)
        return order

    def schedule(self):
        """Seeded permutation blocks: five distinct three-condition orders, or 3:2."""
        if not self.design:
            raise ValueError("legacy plans have no enrollment schedule")
        orders = list(permutations(range(len(self.conditions))))
        random.Random(f"{self.seed}:{self.protocol_id}").shuffle(orders)
        return [list(orders[i % len(orders)]) for i in range(5)]
