"""全件Gridの保存結果を比較 / 選択やTest評価は実行しない。"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.research.results import read_result
from src.infrastructure.research.search import load_study_config
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.comparison import metric_rows, staged_report, verified_result


def comparison_curves(frame, output):
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

    names = []
    for task, metric in (
        ("discovery", "recall"),
        ("repeat", "repeat_recall"),
        ("discovery", "binary_ndcg"),
    ):
        values = frame[(frame.task == task) & (frame.metric == metric)]
        if values.empty:
            raise ValueError(f"Missing comparison metric: {task}/{metric}")
        name, title = ACCURACY_NAMES[task, metric]
        fig, ax = plt.subplots(figsize=(8, 4.8))
        for (similarity, neighbors), group in values.groupby(["similarity", "neighbors"]):
            group = group.sort_values("k")
            style = {20: ("-", "o"), 50: ("--", "s"), 100: (":", "^")}
            line, marker = style.get(neighbors, ("-", "o"))
            ax.plot(
                group.k,
                group["mean"],
                color={"cosine": "#0072B2", "jaccard": "#D55E00"}[similarity],
                linestyle=line,
                marker=marker,
                markersize=4,
                label=f"{similarity.capitalize()} / {neighbors}",
            )
        ax.set(xlabel="K", ylabel=title, title=f"{title}@K", xticks=sorted(values.k.unique()))
        ax.set_ylim(0, max(float(values["mean"].max()) * 1.1, 0.001))
        ax.grid(alpha=0.2)
        ax.legend(title="Similarity / Neighbors", fontsize=9)
        fig.tight_layout()
        filename = f"{name}.png"
        fig.savefig(output / filename, dpi=160)
        plt.close(fig)
        names.append(filename)

        fig, ax = plt.subplots(figsize=(6, 4))
        for similarity, group in values[values.k == 20].groupby("similarity"):
            group = group.sort_values("neighbors")
            ax.plot(
                group.neighbors,
                group["mean"],
                marker="o",
                label=similarity.capitalize(),
                color={"cosine": "#0072B2", "jaccard": "#D55E00"}[similarity],
            )
        ax.set(
            xlabel="Neighbors",
            ylabel=title,
            title=f"{title}@20",
            xticks=sorted(values.neighbors.unique()),
        )
        ax.set_ylim(0, max(float(values.loc[values.k == 20, "mean"].max()) * 1.1, 0.001))
        ax.grid(alpha=0.2)
        ax.legend()
        fig.tight_layout()
        filename = f"{name}_neighbors.png"
        fig.savefig(output / filename, dpi=160)
        plt.close(fig)
        names.append(filename)
    return names


def build_grid_report(config_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.infrastructure.research.grid import grid_variants as variants
    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

    config = load_study_config(config_path)
    rows = []
    sources = []
    reference = None
    for path, _ in variants(config):
        trial = load_study_config(path)
        similarity, neighbors = trial.parameters["similarity"], trial.parameters["neighbors"]
        result, saved, identity, source = verified_result(trial, config.evaluation)
        if reference is None:
            reference = identity
        if identity != reference:
            raise ValueError("Comparison input or evaluation conditions differ")
        sources.append(source)
        rows.extend(
            dict(similarity=similarity, neighbors=neighbors, **score)
            for score in metric_rows(
                result,
                config.evaluation,
                efficiency={
                    "latency_mean",
                    "input_tokens",
                    "output_tokens",
                    "api_cost",
                    "llm_calls",
                    "tool_calls",
                },
            )
        )
    if (config.output / "result.json").exists():
        prior = read_result(config)
        if prior["sources"] != sources:
            raise ValueError("Existing report differs; choose a new output")
        return config.output
    config.output.parent.mkdir(parents=True, exist_ok=True)
    with staged_report(config.output) as output:
        frame = pd.DataFrame(rows)
        frame.to_csv(output / "metrics.csv", index=False, encoding="utf-8-sig")
        atomic_json(
            output / "config.json",
            {
                "format": "music-recsys-full-config-v1",
                "settings": json.loads(json.dumps(asdict(config), default=str)),
                "manifest": reference["manifest"],
                "features_sha256": saved["features_sha256"],
                "population": saved["population"],
            },
        )
        names = comparison_curves(frame, output)
        selected = frame[(frame.k == 20) | frame.k.isna()]
        for (task, metric), values in selected.groupby(["task", "metric"], sort=False):
            if not values["mean"].notna().any():
                continue
            table = values.pivot(index="similarity", columns="neighbors", values="mean")
            name, title = ACCURACY_NAMES.get(
                (task, metric), (f"{metric}_{task}", f"{metric} · {task}")
            )
            fig, ax = plt.subplots(figsize=(5.5, 3))
            picture = ax.imshow(table.to_numpy(float), aspect="auto", cmap="viridis")
            ax.set(
                xticks=range(len(table.columns)),
                xticklabels=table.columns,
                yticks=range(len(table.index)),
                yticklabels=table.index,
                xlabel="Neighbors",
                ylabel="Similarity",
                title=f"{title} @20" if task != "all" else title,
            )
            for i in range(len(table.index)):
                for j in range(len(table.columns)):
                    value = table.iloc[i, j]
                    if pd.notna(value):
                        ax.text(
                            j,
                            i,
                            f"{value:.4f}",
                            ha="center",
                            va="center",
                            color="white",
                            bbox=dict(facecolor="black", alpha=0.4, edgecolor="none", pad=2),
                        )
            fig.colorbar(picture, ax=ax)
            fig.tight_layout()
            filename = f"{name}_heatmap.png"
            fig.savefig(output / filename, dpi=160)
            plt.close(fig)
            names.append(filename)
        atomic_json(
            output / "result.json",
            dict(
                format="music-recsys-grid-comparison-v1",
                selection="not_selected",
                sources=sources,
                status="succeeded",
                model="itemcf",
                population=saved["population"],
                window_days=config.evaluation.primary_window_days,
                day_weight=config.evaluation.primary_day_weight,
                figure_k=20,
                figures=names,
                metrics=json.loads(frame.to_json(orient="records", double_precision=15)),
            ),
        )
        (output / "run.log").write_text(
            "SUCCEEDED: verified Validation comparisons; no setting selected\n"
        )
    return config.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(build_grid_report(parser.parse_args().config))


if __name__ == "__main__":
    main()
