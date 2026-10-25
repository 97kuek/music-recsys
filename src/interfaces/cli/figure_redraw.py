"""Redraw pruned individual figures from saved numerical evidence to a new directory."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.figure_retention import SUPPORTED, retention_record
from src.infrastructure.research.store import atomic_json


def redraw(result_path, output, *, filenames=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from src.infrastructure.provenance import source_hash
    from src.interfaces.notebook.popularity_figures import condition_figures

    result_path, output = Path(result_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Redraw output must be new; saved files are never overwritten")
    record = retention_record(result_path.parent)
    if record is None:
        raise ValueError("An explicit figure retention record is required")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    names = list(filenames if filenames is not None else record["removed_files"])
    if not names or len(set(names)) != len(names):
        raise ValueError("Choose unique recorded figures")
    for name in names:
        path = Path(name)
        if (
            path.name != name
            or path.stem not in SUPPORTED
            or path.suffix not in {".png", ".pdf"}
            or name not in record["removed_files"]
        ):
            raise ValueError("Choose only explicitly pruned figures")
    drawn = condition_figures(result)
    try:
        if any(Path(name).stem not in drawn for name in names):
            raise ValueError("Saved result has no measured data for a requested figure")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="figure-redraw-", dir=output.parent) as temporary:
            stage = Path(temporary) / "report"
            stage.mkdir()
            with matplotlib.rc_context({"pdf.fonttype": 42, "ps.fonttype": 42}):
                for name in names:
                    drawn[Path(name).stem].savefig(stage / name, dpi=300)
            references = {name: dict(path=name, sha256=file_sha256(stage / name)) for name in names}
            atomic_json(
                stage / "config.json",
                dict(
                    format="music-recsys-figure-redraw-config-v1",
                    source_result=str(result_path),
                    source_result_sha256=file_sha256(result_path),
                    retention=str(result_path.parent / "figures.retention.json"),
                    retention_sha256=file_sha256(result_path.parent / "figures.retention.json"),
                    render_source_sha256=source_hash(),
                    figures=names,
                ),
            )
            atomic_json(
                stage / "result.json",
                dict(
                    format="music-recsys-figure-redraw-v1",
                    status="succeeded",
                    artifacts=references,
                    note="Numerical means/CI/bin counts come from the unchanged original result; no training or scoring; image bytes and layout may differ from the pruned images",
                ),
            )
            (stage / "run.log").write_text(
                "SUCCEEDED redraw from saved numerical result; original experiment preserved\n",
                encoding="utf-8",
            )
            os.replace(stage, output)
    finally:
        for figure in drawn.values():
            plt.close(figure)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--figures", nargs="+")
    args = parser.parse_args()
    print(redraw(args.result, args.output, filenames=args.figures))


if __name__ == "__main__":
    main()
