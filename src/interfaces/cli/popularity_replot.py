"""Replace Test PNGs from verified saved results; preserve scoring evidence."""

import json
import os
import shutil
import tempfile
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.paths import PROJECT_ROOT


def _verified(configs, output):
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    summary = read(output / "result.json")
    if summary["status"] != "succeeded" or summary["phase"] != "test":
        raise ValueError("A successful saved final Test is required")
    if summary["protocol_sha256"] != file_sha256(output / "config.json"):
        raise ValueError("Frozen Test protocol changed")
    verify_artifacts(output, summary["artifacts"])
    results, inputs = [], {}
    references = summary["conditions"]
    if [c.output.name for c in configs] != [r["condition"] for r in references]:
        raise ValueError("Saved Test conditions differ")
    for c, reference in zip(configs, references, strict=True):
        directory = c.output / "test"
        for role in ("config", "result"):
            path = directory / f"{role}.json"
            if (
                Path(reference[role]).resolve() != path.resolve()
                or file_sha256(path) != reference[role + "_sha256"]
            ):
                raise ValueError("Saved Test evidence changed")
        saved = read(directory / "config.json")
        expected = json.loads(json.dumps(asdict(replace(c, output=directory)), default=str))
        if saved["settings"] != expected:
            raise ValueError("Current configuration differs from saved Test")
        result = read(directory / "result.json")
        if result["status"] != "succeeded" or result["phase"] != "test":
            raise ValueError("Both Test conditions must have succeeded")
        if result["population"] != summary["population"]:
            raise ValueError("Test populations differ")
        verify_artifacts(directory, result["artifacts"])
        results.append(result)
    for directory in (output, *(c.output / "test" for c in configs)):
        for name in ("config.json", "result.json", "metrics.csv"):
            path = directory / name
            inputs[str(path)] = file_sha256(path)
    return summary, results, inputs


def replot(config_paths, *, archive_root=None):
    """Stage all PNGs before publication; roll back if replacement fails."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from src.infrastructure.provenance import source_hash
    from src.interfaces.notebook.popularity_figures import comparison_figures, condition_figures

    configs = [load_study_config(p) for p in config_paths]
    if (
        len(configs) != 2
        or any(c.model != "popularity" or c.grid for c in configs)
        or [c.parameters for c in configs] != [{}, {"basis": "user_count"}]
        or len({c.output.parent for c in configs}) != 1
    ):
        raise ValueError("Pass the two fixed Popularity configurations, plays then users")
    output = configs[0].output.parent / "test"
    if not all(
        (p / "result.json").is_file() for p in [output, *(c.output / "test" for c in configs)]
    ):
        raise ValueError("Successful saved final Test outputs are required")
    with ExitStack() as stack:
        for directory in (output, *(c.output / "test" for c in configs)):
            stack.enter_context(study_lock(replace(configs[0], output=directory)))
        summary, results, inputs = _verified(configs, output)
        source = source_hash()
        archive_root = Path(archive_root or PROJECT_ROOT / "data/provenance")
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = Path(tempfile.mkdtemp(prefix="popularity-figures-", dir=archive_root))
        records = []
        manifest = dict(
            format="music-recsys-figure-render-v1",
            status="preparing",
            created_at=datetime.now(UTC).isoformat(),
            render_source_sha256=source,
            matplotlib_version=matplotlib.__version__,
            rescored=False,
            inputs=inputs,
            figures=records,
        )
        atomic_json(archive / "manifest.json", manifest)
        try:
            with tempfile.TemporaryDirectory(prefix=".figures-", dir=output.parent) as staging:
                groups = [
                    (c.output / "test", lambda r=r: condition_figures(r))
                    for c, r in zip(configs, results, strict=True)
                ]
                groups.append(
                    (
                        output,
                        lambda: comparison_figures(configs, results, summary["paired_differences"]),
                    )
                )
                for directory, build in groups:
                    figures = build()
                    try:
                        for name, figure in figures.items():
                            target = directory / f"{name}.png"
                            relative = target.relative_to(output.parent)
                            staged = Path(staging) / relative
                            staged.parent.mkdir(parents=True, exist_ok=True)
                            figure.savefig(staged, dpi=300)
                            previous_hash = file_sha256(target) if target.exists() else None
                            if target.exists():
                                original = archive / relative
                                original.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(target, original)
                            records.append(
                                dict(
                                    path=str(target),
                                    previous_sha256=previous_hash,
                                    sha256=file_sha256(staged),
                                    archived=str(relative) if previous_hash else None,
                                )
                            )
                    finally:
                        for figure in figures.values():
                            plt.close(figure)
                if source != source_hash() or any(file_sha256(p) != h for p, h in inputs.items()):
                    raise ValueError("Saved evidence or rendering source changed during rendering")
                for record in records:
                    path = Path(record["path"])
                    actual = file_sha256(path) if path.exists() else None
                    if actual != record["previous_sha256"]:
                        raise ValueError("An existing figure changed during rendering")
                manifest["status"] = "prepared"
                atomic_json(archive / "manifest.json", manifest)
                published = []
                try:
                    for record in records:
                        path = Path(record["path"])
                        relative = path.relative_to(output.parent)
                        os.replace(Path(staging) / relative, path)
                        published.append(record)
                except BaseException:
                    for record in reversed(published):
                        path = Path(record["path"])
                        if record["archived"]:
                            shutil.copy2(archive / record["archived"], path)
                        else:
                            path.unlink()
                    raise
            manifest["status"] = "succeeded"
            atomic_json(archive / "manifest.json", manifest)
            with (output / "run.log").open("a", encoding="utf-8") as log:
                log.write(
                    f"REPLOT: saved estimates unchanged; figure provenance: {archive / 'manifest.json'}\n"
                )
        except BaseException:
            manifest["status"] = "failed"
            atomic_json(archive / "manifest.json", manifest)
            raise
    return archive / "manifest.json"
