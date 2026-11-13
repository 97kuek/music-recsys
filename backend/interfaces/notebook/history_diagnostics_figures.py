"""One paired-difference metric per diagnostic figure."""


def figures(output, rows):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for metric in ("graded_ndcg", "repeat_recall"):
        fig, ax = plt.subplots(figsize=(6, 4))
        for phase in ("validation", "test"):
            selected = [
                r
                for r in rows
                if r["phase"] == phase
                and r["task"] == "repeat"
                and r["window_days"] == 90
                and r["k"] == 20
                and r["metric"] == metric
                and r["day_weight"] in (None, 1)
                and r["stratum"] != "all"
            ]
            selected.sort(key=lambda r: r["stratum"])
            ax.plot(
                [r["stratum"] for r in selected],
                [r["difference"] for r in selected],
                marker="o",
                label=phase,
            )
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set(
            xlabel="Learning-history track quartile (Validation cutoffs)",
            ylabel=f"Difference: {metric}",
        )
        ax.legend()
        fig.tight_layout()
        fig.savefig(output / f"{metric}.png", dpi=160)
        plt.close(fig)
