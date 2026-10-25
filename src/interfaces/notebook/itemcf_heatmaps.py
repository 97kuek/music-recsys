"""Discrete similarity/neighbor grids with measured values and no interpolation."""

from src.interfaces.notebook.scientific_plots import axes


def heatmaps(frame, similarities, neighbors):
    import numpy as np

    result = {}
    for task, metric, name, label in (
        ("discovery", "recall", "recall", "New-track recall@20 (%)"),
        ("repeat", "repeat_recall", "repeat_recall", "Repeat recall@20 (%)"),
    ):
        selected = frame[
            (frame.task == task)
            & (frame.metric == metric)
            & (frame.k == 20)
            & (frame.window_days == 90)
        ]
        if selected.duplicated(["similarity", "neighbors"]).any():
            raise ValueError("Duplicate heatmap cell")
        values = (
            selected.pivot(index="similarity", columns="neighbors", values="mean").reindex(
                index=similarities, columns=neighbors
            )
            * 100
        )
        if not values.notna().any().any():
            continue
        fig, ax = axes(figsize=(max(7.4, 0.72 * len(neighbors) + 1.8), 3.1))
        ax.grid(False)
        artist = ax.imshow(
            np.ma.masked_invalid(values.to_numpy(dtype=float)),
            interpolation="nearest",
            aspect="auto",
            cmap="cividis",
            vmin=0,
        )
        labels = {"cosine": "Cosine", "jaccard": "Jaccard", "pearson": r"Pearson ($\phi$)"}
        ax.set(
            xticks=range(len(neighbors)),
            xticklabels=[str(n) for n in neighbors],
            yticks=range(len(similarities)),
            yticklabels=[labels[s] for s in similarities],
            xlabel=r"Number of neighbors, $n$ (tested levels)",
            ylabel="Similarity measure",
        )
        ax.set_xticks(np.arange(len(neighbors) + 1) - 0.5, minor=True)
        ax.set_yticks(np.arange(len(similarities) + 1) - 0.5, minor=True)
        ax.grid(which="minor", color="white", linewidth=0.8)
        ax.tick_params(which="minor", bottom=False, left=False)
        for i in range(len(similarities)):
            for j in range(len(neighbors)):
                value = values.iloc[i, j]
                color = "white" if np.isfinite(value) and artist.norm(value) < 0.45 else "black"
                ax.text(
                    j,
                    i,
                    f"{value:.2f}" if np.isfinite(value) else "—",
                    ha="center",
                    va="center",
                    fontsize=9,
                    color=color,
                )
        fig.colorbar(artist, ax=ax, label=label, pad=0.02)
        result[name] = fig
    return result


def save_heatmaps(output, frame, similarities, neighbors):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    drawn = heatmaps(frame, similarities, neighbors)
    try:
        with matplotlib.rc_context({"pdf.fonttype": 42, "ps.fonttype": 42}):
            for name, fig in drawn.items():
                fig.savefig(output / f"{name}.png", dpi=300)
                fig.savefig(output / f"{name}.pdf", metadata={"Title": ""})
    finally:
        for fig in drawn.values():
            plt.close(fig)
    return list(drawn)
