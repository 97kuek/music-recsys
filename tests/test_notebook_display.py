import base64
import io
import re
from types import SimpleNamespace

import pandas as pd

from music_recsys.notebook_display import metric_groups, table_html


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


def test_primary_metric_unset_does_not_promote_auxiliary(listening_study):
    from test_listening_workflow import prepared

    from music_recsys.search_analysis import selected_curves

    config, _ = prepared(listening_study)
    frame = selected_curves(config, resamples=100)
    primary, auxiliary = metric_groups(frame, config)
    assert set(primary.task) == {config.task}
    assert set(primary.metric) == {config.metric}
    assert not ((auxiliary.task == config.task) & (auxiliary.metric == config.metric)).any()
    empty, diagnostic = metric_groups(frame, SimpleNamespace(task=None, metric=None))
    assert empty.empty
    assert len(diagnostic) == len(primary) + len(auxiliary)
