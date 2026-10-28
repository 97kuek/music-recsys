"""Save publication PNG/PDF figures and release resources even on write failure."""


def save(fig, output, name):
    import matplotlib as mpl
    import matplotlib.pyplot as plt

    try:
        with mpl.rc_context({"pdf.fonttype": 42, "ps.fonttype": 42}):
            fig.savefig(output / f"{name}.png", dpi=300)
            fig.savefig(output / f"{name}.pdf", metadata={"Title": ""})
    finally:
        plt.close(fig)
    return f"{name}.png"
