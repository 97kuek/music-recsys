"""専用Compose用の起動口。ホストへの公開範囲はComposeのloopback mappingで限定。"""

import argparse
import os
from pathlib import Path

import uvicorn

from src.infrastructure.config.settings import setting
from src.interfaces.cli.online_bundle import verify
from src.interfaces.http.server import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audience", choices=("participant", "researcher"), default="participant")
    args = parser.parse_args()
    os.umask(0o077)
    config = Path("/bundle/config.json")
    study = Path("/bundle/study.json")
    verify(config.parent)
    app = create_app(
        config,
        Path("/state/sessions.sqlite"),
        audience=args.audience,
        study_path=study if study.is_file() and args.audience == "participant" else None,
        model_version=setting("MODEL_VERSION") or None,
        control_path=(setting("CONTROL_CONFIG") or None) if args.audience == "researcher" else None,
    )
    uvicorn.run(app, host="0.0.0.0", port=8765, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
