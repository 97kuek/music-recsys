from dataclasses import replace

from src.features import write_features

MODELS = ("popularity", "itemcf", "usercf", "als", "bpr", "content", "hybrid")


def model_config(config, model):
    feature = config.path.parent / "features.json"
    if not feature.exists():
        from src.experiment_search import context

        data, _ = context(config)
        write_features(
            feature,
            dataset_id=data.dataset_id,
            snapshot_id=data.provenance["snapshot_id"],
            source={"kind": "synthetic"},
            features={
                item: {"rock" if i % 2 else "jazz": 1.0}
                for i, item in enumerate(sorted(data.catalog_ids))
            },
        )
    grid, params = {}, {}
    if model in ("itemcf", "usercf"):
        grid = {"neighbors": [1, 2]}
    elif model in ("als", "bpr"):
        grid, params = {"factors": [2, 3]}, {"iterations": 2}
    elif model == "content":
        grid = {"use_idf": [False, True]}
    elif model == "hybrid":
        grid = {"cf_weight": [0.0, 0.5]}
    return replace(
        config,
        name=f"suite-{model}",
        model=model,
        parameters=params,
        grid=grid,
        stage="A",
        features=feature,
        output=config.output.parent / f"{model}-runs",
    )
