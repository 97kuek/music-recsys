"""公開用の合成デモだけを静的アセットへ変換する。実カタログ・実モデルは拒否。"""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from src.listening_data import file_sha256
from src.model_artifacts import load_model


def export(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    config = json.loads(config_path.read_text())
    if config.get("mode") != "demo":
        raise ValueError("public export requires synthetic demo mode")
    catalog = config_path.parent / config["catalog"]["path"]
    if file_sha256(catalog) != config["catalog"]["sha256"]:
        raise ValueError("catalog checksum mismatch")
    models = [
        load_model(config_path.parent / m["path"], expected_manifest_sha256=m["sha256"])
        for m in config["models"]
    ]
    spec, model = next((s, m) for s, m in models if s.version_id == "demo-itemcf")
    if spec.algorithm != "itemcf" or any(not i.startswith("sample-") for i in model.index):
        raise ValueError("public export requires the explicit sample ItemCF model")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".public-demo-", dir=output.parent))
    try:
        tracks = json.loads(catalog.read_text())
        for i, row in enumerate(tracks):
            item = row["item_id"]
            meta = config["audio"][item]
            path = (config_path.parent / meta["path"]).resolve()
            if (
                not meta["sample"]
                or meta["license_ref"] != "original-procedural-sample-CC0-1.0"
                or not path.is_relative_to(config_path.parent)
                or file_sha256(path) != meta["sha256"]
            ):
                raise ValueError("only verified original CC0 demo audio can be public")
            target = stage / "audio" / f"{i}.wav"
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
            row.update(
                sample=True,
                provider="sample",
                playback_status="available",
                url=f"/demo/audio/{i}.wav",
            )
        arrays = model.arrays
        (stage / "demo.json").write_text(
            json.dumps(
                {
                    "tracks": tracks,
                    "items": arrays["items"].tolist(),
                    "plays": arrays["plays"].tolist(),
                    "targets": arrays["targets"].tolist(),
                    "weights": arrays["weights"].tolist(),
                    "catalog_key": config["catalog"]["sha256"],
                },
                ensure_ascii=False,
            )
        )
        shutil.copyfile(config_path.parent / "LICENSE.txt", stage / "LICENSE.txt")
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.config, args.output)


if __name__ == "__main__":
    main()
