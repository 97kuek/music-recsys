"""One candidate-diagnostic metric per figure; retain empty bands without zero filling."""


def figures(rows, output):
    import matplotlib.pyplot as plt

    for group in sorted({r["group"] for r in rows}):
        for task in ("new", "repeat"):
            for kind in ("history", "popularity"):
                for metric in ("candidate_recall", "recall_difference"):
                    selected = [
                        r
                        for r in rows
                        if r["group"] == group
                        and r["task"] == task
                        and r["stratum_kind"] == kind
                        and r["window_days"] == 90
                        and r["metric"] == metric
                    ]
                    if not any(r["mean"] is not None for r in selected):
                        continue
                    fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")
                    for scope in dict.fromkeys(r["scope"] for r in selected):
                        values = {r["stratum"]: r["mean"] for r in selected if r["scope"] == scope}
                        ax.plot(
                            range(1, 5),
                            [values.get(f"Q{i}") for i in range(1, 5)],
                            marker="o",
                            label=scope,
                        )
                    ax.set(
                        xticks=range(1, 5),
                        xticklabels=["Q1", "Q2", "Q3", "Q4"],
                        xlabel=f"{kind} quartile (learning only)",
                        ylabel=metric,
                        title=f"{group} / {task} / 90 days",
                    )
                    ax.legend(fontsize="small")
                    fig.savefig(output / f"{group}_{task}_{kind}_{metric}.png", dpi=150)
                    plt.close(fig)
