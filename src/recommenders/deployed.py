"""全件学習済み配列を共通recommend契約へ接続する推論専用Adapter。"""

import numpy as np

from src.candidates import eligible_candidates
from src.recommenders.als import als_solve
from src.types import RecommendationResponse


class DeployedRecommender:
    def __init__(self, spec, arrays, *, als_fold_in=False):
        if type(als_fold_in) is not bool:
            raise ValueError("als_fold_in must be boolean")
        self.spec, self.arrays, self.als_fold_in = spec, arrays, als_fold_in
        items = arrays["items"]
        if items.ndim != 1 or items.dtype.kind != "U" or not len(items):
            raise ValueError("items must be nonempty string vector")
        if list(items) != sorted(set(items)) or any(not item.strip() for item in items):
            raise ValueError("items must be sorted unique nonempty IDs")
        self.index = {item: i for i, item in enumerate(items)}
        n = len(items)
        plays = arrays["plays"]
        if (
            plays.shape != (n,)
            or plays.dtype.kind not in "fiu"
            or not np.isfinite(plays).all()
            or (plays <= 0).any()
        ):
            raise ValueError("invalid popularity counts")
        algorithm = spec.algorithm
        if algorithm == "popularity":
            scores = arrays["scores"]
            if (
                scores.shape != (n,)
                or scores.dtype.kind not in "fiu"
                or not np.isfinite(scores).all()
                or (scores <= 0).any()
            ):
                raise ValueError("invalid popularity scores")
        elif algorithm == "itemcf":
            targets, weights = arrays["targets"], arrays["weights"]
            if targets.ndim != 2 or targets.shape[0] != n or weights.shape != targets.shape:
                raise ValueError("invalid neighbor shapes")
            if targets.dtype.kind not in "iu" or (targets < 0).any() or (targets >= n).any():
                raise ValueError("invalid neighbor indices")
            if weights.dtype.kind != "f" or not np.isfinite(weights).all() or (weights < 0).any():
                raise ValueError("invalid neighbor weights")
            if targets.shape[1] != min(spec.parameters["neighbors"], max(0, n - 1)):
                raise ValueError("neighbor count differs from manifest")
        elif algorithm in {"als", "bpr"}:
            users = arrays["users"]
            if (
                users.ndim != 1
                or users.dtype.kind != "U"
                or list(users) != sorted(set(users))
                or any(not u.strip() for u in users)
            ):
                raise ValueError("invalid user index")
            self.users = {u: i for i, u in enumerate(users)}
            width = spec.parameters["factors"] + (algorithm == "bpr")
            for key, rows in (("item_factors", n), ("user_factors", len(users))):
                array = arrays[key]
                if (
                    array.shape != (rows, width)
                    or array.dtype.kind != "f"
                    or not np.isfinite(array).all()
                ):
                    raise ValueError("invalid factor shape or values")
            if algorithm == "bpr" and (
                arrays["active_users"].shape != (len(users),)
                or arrays["active_users"].dtype.kind != "b"
            ):
                raise ValueError("invalid active user mask")
        else:
            raise ValueError("unsupported algorithm")
        self._gram = (
            arrays["item_factors"].T @ arrays["item_factors"]
            if algorithm == "als" and als_fold_in
            else None
        )

    def route(self, request):
        """実際の推論経路。オンライン条件の記録に使用する。"""
        algorithm = self.spec.algorithm
        known_history = any(item in self.index for item in request.history)
        if algorithm == "popularity":
            return "popularity"
        if algorithm == "itemcf":
            return "itemcf_history" if known_history else "popularity_fallback"
        if (
            request.user_id in self.users
            and getattr(request, "input_kind", None) != "initial_profile"
        ):
            if algorithm == "bpr" and not self.arrays["active_users"][self.users[request.user_id]]:
                return "popularity_fallback"
            return "trained_user"
        if algorithm == "als" and self.als_fold_in and known_history:
            return "als_fold_in_binary_seeds"
        return "popularity_fallback"

    def recommend(self, request):
        a = self.arrays
        candidates = np.array(
            sorted(self.index[i] for i in eligible_candidates(request) if i in self.index),
            dtype=np.int64,
        )
        if not len(candidates):
            return RecommendationResponse(request.request_id, ())
        route = self.route(request)
        history = np.array(
            sorted(self.index[i] for i in request.history if i in self.index), dtype=np.int64
        )
        if route == "popularity":
            scores = a["scores"][candidates]
        elif route == "itemcf_history":
            scores = np.bincount(
                a["targets"][history].ravel(),
                weights=a["weights"][history].ravel(),
                minlength=len(a["items"]),
            )[candidates]
        elif route == "trained_user":
            scores = a["item_factors"][candidates] @ a["user_factors"][self.users[request.user_id]]
        elif route == "als_fold_in_binary_seeds":
            params = self.spec.parameters
            vector = als_solve(
                a["item_factors"],
                [(i, 1) for i in history],
                alpha=params["alpha"],
                regularization=params["regularization"],
                confidence_transform=params["confidence_transform"],
                gram=self._gram,
            )
            scores = a["item_factors"][candidates] @ vector
        else:
            scores = a["plays"][candidates]
        if not np.isfinite(scores).all():
            raise ValueError("nonfinite inference scores")
        # Popularityは同点時にitem IDのみ。他の全件モデルはplays→ID。
        tie = np.zeros(len(candidates)) if route == "popularity" else a["plays"][candidates]
        order = np.lexsort((candidates, -tie, -scores))[: request.top_k]
        return RecommendationResponse(
            request.request_id, tuple(a["items"][candidates[order]].tolist())
        )
