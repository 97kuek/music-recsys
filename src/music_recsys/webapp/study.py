"""明示指定した予備実験計画。モデル選定や実験実施の承認を自動化しない。"""

import hashlib
import json
import random
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    condition_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=200)
    offline_run_ref: str | None = None
    required_route: (
        Literal[
            "popularity",
            "itemcf_history",
            "trained_user",
            "als_fold_in_binary_seeds",
            "popularity_fallback",
        ]
        | None
    ) = None


class StudyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_id: str = Field(min_length=1, max_length=100)
    seed: int
    top_k: int = Field(ge=2, le=20)
    conditions: list[Condition] = Field(min_length=2, max_length=8)

    @classmethod
    def read(cls, path):
        plan = cls.model_validate(json.loads(Path(path).read_text()))
        ids = [c.condition_id for c in plan.conditions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate condition")
        return plan

    @property
    def digest(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def order(self, participant):
        # 順序は仮名ID＋seedで再現できる。少人数で均等割付を保証するものではない。
        order = list(range(len(self.conditions)))
        random.Random(f"{self.seed}:{participant}").shuffle(order)
        return order
