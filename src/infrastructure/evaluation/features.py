"""Shared float64 feature-prefix metrics for evaluation and candidate diagnostics."""

import numpy as np

from src.domain.recommendation.features import unit_vector, validate_features


class FeaturePrefixes:
    """Cosine ILD via prefix vector sums, avoiding a large all-item Gram matrix."""

    def __init__(self, features, items, xp=np):
        from scipy.sparse import coo_matrix

        validate_features(features)
        self.xp = xp
        tags = {t: i for i, t in enumerate(sorted({t for v in features.values() for t in v}))}
        rows, cols, data, valid = [], [], [], []
        for i, item in enumerate(items):
            vector = unit_vector(features.get(item, {}))
            valid.append(bool(vector))
            for tag, value in vector.items():
                rows.append(i)
                cols.append(tags[tag])
                data.append(value)
        matrix = coo_matrix((data, (rows, cols)), shape=(len(items), max(1, len(tags)))).tocsr()
        if xp is not np:
            from cupyx.scipy.sparse import csr_matrix

            matrix = csr_matrix(matrix)
        self.matrix, self.valid = matrix, xp.asarray(valid)

    def calculate(self, rankings):
        xp = self.xp
        batch, models, width = rankings.shape
        values = xp.zeros((batch, models, width), dtype=xp.float64)
        valid = xp.zeros((batch, models, width), dtype=bool)
        vector_sum = type(self.matrix)((batch * models, self.matrix.shape[1]), dtype=xp.float64)
        norm_sum = xp.zeros(batch * models, dtype=xp.float64)
        lengths = xp.zeros((batch, models), dtype=xp.int32)
        complete = xp.ones((batch, models), dtype=bool)
        for rank in range(width):
            ids = rankings[:, :, rank]
            present = ids >= 0
            safe = xp.maximum(ids, 0)
            vector = self.matrix[safe.reshape(-1)].multiply(present.reshape(-1, 1)).tocsr()
            vector_sum = vector_sum + vector
            norm_sum += xp.asarray(vector.multiply(vector).sum(axis=1)).reshape(-1)
            lengths += present
            complete &= ~present | self.valid[safe]
            denominator = xp.maximum(lengths * (lengths - 1), 1)
            squared_sum = xp.asarray(vector_sum.multiply(vector_sum).sum(axis=1)).reshape(-1)
            cosine_sum = (squared_sum - norm_sum).reshape(batch, models)
            values[:, :, rank] = xp.clip(1 - cosine_sum / denominator, 0, 1)
            valid[:, :, rank] = (lengths >= 2) & complete
        return values, valid
