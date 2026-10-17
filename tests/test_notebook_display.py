import base64
import io
import re

import pandas as pd

from src.interfaces.notebook.evaluation_views import (
    accuracy_groups,
    diversity_table,
    efficiency_table,
)
from src.interfaces.notebook.notebook_display import table_html


def test_csv_retains_every_row_precision_and_column_order():
    frame = pd.DataFrame(
        {"score": [0.123456789012345, None] * 200, "item_id": ["<script>曲</script>"] * 400}
    )
    html = table_html(frame, "<値>", "results")
    encoded = re.search(r"data:text/csv;base64,([^\"]+)", html)[1]
    csv = base64.b64decode(encoded).decode("utf-8-sig")
    result = pd.read_csv(io.StringIO(csv), float_precision="round_trip")
    pd.testing.assert_frame_equal(result, frame)
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "<details>" in html and "overflow:auto" in html


def test_fixed_metrics_do_not_depend_on_selection_objective(listening_study):
    from test_listening_workflow import prepared

    from src.interfaces.notebook.search_analysis import selected_curves

    config, _ = prepared(listening_study)
    frame = selected_curves(config, resamples=100)
    groups = accuracy_groups(frame)
    assert set(groups["新規発見"].metric) == {"binary_ndcg", "recall"}
    assert set(groups["継続的な嗜好"].metric) == {"graded_ndcg", "repeat_recall"}
    assert set(groups["継続的な嗜好"].task) == {"repeat", "retention_proxy"}
    for rows in groups.values():
        assert set(rows.k) == set(range(2, 21))
    diversity = diversity_table(frame)
    assert len(diversity) == 38
    assert set(diversity.task) == {"new", "repeat"}
    assert set(diversity.k) == set(range(2, 21))
    efficiency = efficiency_table(frame)
    assert len(efficiency) == 1
    assert efficiency.input_tokens.isna().all()
    assert efficiency.latency_mean.notna().all()
