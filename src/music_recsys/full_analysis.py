"""全件結果の読込み / 個別イベントや全候補をメモリへ展開しない。"""

import json
from dataclasses import asdict


def is_full(config):
    path = config.output / "config.json"
    return (
        path.exists()
        and json.loads(path.read_text()).get("format") == "music-recsys-full-config-v1"
    )


def read_result(config):
    from music_recsys.listening_data import file_sha256, read_bundle

    saved = json.loads((config.output / "config.json").read_text())
    if saved["settings"] != json.loads(json.dumps(asdict(config), default=str)):
        raise ValueError("保存結果と現在の設定が異なります")
    manifest, _ = read_bundle(config.validation_dataset)
    if saved["manifest"] != manifest or saved["features_sha256"] != file_sha256(config.features):
        raise ValueError("保存結果と現在の入力が異なります")
    return json.loads((config.output / "result.json").read_text())


def curves(config):
    import pandas as pd

    document = read_result(config)
    frame = pd.DataFrame(document["accuracy"])
    frame["model"] = document["model"]
    frame["diversity_task"] = frame.task.replace({"discovery": "new", "retention_proxy": "new"})
    beyond = pd.DataFrame(document["diversity"]).rename(columns={"task": "diversity_task"})
    frame = frame.merge(beyond, on=["diversity_task", "k"], how="left")
    for key, value in document["efficiency"].items():
        frame[key] = value
    frame.attrs["primary"] = {
        "window_days": config.evaluation.primary_window_days,
        "day_weight": config.evaluation.primary_day_weight,
    }
    return frame


def overview(config):
    import duckdb

    from music_recsys.listening_data import validate_bundle

    manifest, paths, counts = validate_bundle(config.validation_dataset)
    with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as c:
        c.read_parquet(str(paths["learning"])).create_view("learning")
        c.read_parquet(str(paths["observations"])).create_view("observations")
        frame = c.execute(
            """WITH histories AS (SELECT user_id,count(*) history_count FROM learning GROUP BY user_id),
        positives AS (SELECT user_id,CASE WHEN learning_play_count>0 THEN 'repeat' ELSE 'new' END task,count(*) positive_count FROM observations WHERE window_days=? GROUP BY user_id,task)
        SELECT t.task||':'||h.user_id request_id,h.user_id,t.task,h.history_count,
        CASE WHEN t.task='repeat' THEN h.history_count ELSE ?-h.history_count END candidate_count,
        coalesce(p.positive_count,0) positive_count FROM histories h CROSS JOIN (VALUES ('new'),('repeat')) t(task)
        LEFT JOIN positives p ON p.user_id=h.user_id AND p.task=t.task ORDER BY h.user_id,t.task""",
            [config.evaluation.primary_window_days, counts["items"]],
        ).df()
    task = "repeat" if config.task == "repeat" else "new"
    selected = frame[frame.task == task]
    return (
        manifest,
        frame,
        dict(
            phase="validation",
            protocol="listening-v2",
            window_days=config.evaluation.primary_window_days,
            task=task,
            requests=len(frame),
            users=counts["users"],
            evaluable_users=int((selected.positive_count > 0).sum()),
            empty_truth_requests=int((selected.positive_count == 0).sum()),
            training_events=counts["events"],
            catalog_items=counts["items"],
        ),
    )
