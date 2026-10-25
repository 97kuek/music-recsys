"""Shared notation for measured, discrete K values and publication figures."""

STYLES = (
    dict(color="#0072B2", marker="o", linestyle="-"),
    dict(color="#D55E00", marker="s", linestyle="--"),
    dict(color="#009E73", marker="^", linestyle="-."),
    dict(color="#CC79A7", marker="D", linestyle=":"),
    dict(color="#E69F00", marker="v", linestyle="--"),
)
LABELS = {
    "plays": "Total play count",
    "users": "Unique listener count",
    "new": "New tracks",
    "repeat": "Previously heard tracks",
}


def label_text(value):
    if isinstance(value, tuple):
        return " / ".join(label_text(v) for v in value)
    return LABELS.get(value, str(value))


def axes(*, figsize=(6.4, 4.0)):
    import matplotlib as mpl
    import matplotlib.pyplot as plt

    with mpl.rc_context(
        {
            "font.family": "serif",
            "font.serif": ["DejaVu Serif"],
            "font.size": 11,
            "axes.labelsize": 12,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 9,
            "mathtext.fontset": "dejavuserif",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    ):
        fig, ax = plt.subplots(figsize=figsize, layout="constrained")
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="0.9", linewidth=0.6)
    return fig, ax


def metric_label(metric, task=None):
    if metric == "binary_ndcg":
        return r"New-track binary NDCG@$K$", 1
    if metric == "recall":
        return r"New-track recall@$K$ (%)", 100
    if metric == "repeat_recall":
        return r"Repeat recall@$K$ (%)", 100
    if metric == "graded_ndcg":
        prefix = "New-track" if task == "retention_proxy" else "Previously heard-track"
        return prefix + r" graded NDCG@$K$", 1
    return {
        "ild": (r"Intra-list diversity@$K$", 1),
        "catalog_coverage": (r"Catalog coverage@$K$ (%)", 100),
        "novelty": (r"Novelty@$K$ (bits)", 1),
    }[metric]


def plot_k(ax, values, column, *, index=0, label=None, scale=1, interval=False):
    """Missing estimates break lines; intervals exist only at measured K values."""
    values = values.sort_values("k")
    ax.plot(
        values.k,
        values[column] * scale,
        label=label_text(label) if label is not None else None,
        markersize=3.5,
        linewidth=1.1,
        markerfacecolor="white",
        **STYLES[index % len(STYLES)],
    )
    if interval and {"ci_low", "ci_high"}.issubset(values.columns):
        ci = values.dropna(subset=[column, "ci_low", "ci_high"])
        if not ci.empty:
            ax.errorbar(
                ci.k,
                ci[column] * scale,
                yerr=[(ci[column] - ci.ci_low) * scale, (ci.ci_high - ci[column]) * scale],
                fmt="none",
                ecolor=STYLES[index % len(STYLES)]["color"],
                capsize=2,
                linewidth=0.8,
            )


def finish_k(ax, ks, ylabel, *, upper=None):
    from matplotlib.ticker import MaxNLocator

    ks = sorted({int(k) for k in ks})
    ax.set(xlabel=r"Recommendation cutoff, $K$", ylabel=ylabel)
    ax.set_xticks([k for k in ks if k % 2 == 0] or ks)
    ax.set_xticks(ks, minor=True)
    ax.set_xlim(min(ks) - 0.4, max(ks) + 0.4)
    ax.set_ylim(bottom=0, top=upper)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=3))
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(frameon=False)


def save_pngs(output, draw):
    """Save each named figure separately and release every figure after rendering."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    drawn = draw()
    try:
        for name, figure in drawn.items():
            figure.savefig(output / f"{name}.png", dpi=300)
    finally:
        for figure in drawn.values():
            plt.close(figure)
    return [f"{name}.png" for name in drawn]
