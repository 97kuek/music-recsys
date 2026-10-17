"""現作業ツリーの配信コードと検証済みbundleを明示リストで梱包する。"""

import argparse
import re
import zipfile
from pathlib import Path

from src.interfaces.cli.online_bundle import verify

ROOT_FILES = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "NOTICE",
    "AGENTS.md",
    "README.en.md",
    ".dockerignore",
    "deploy/Dockerfile",
    "deploy/compose.yaml",
    "deploy/mac.sh",
    "deploy/MusicRecsys.command",
    "deploy/cloudflared.example.yml",
    "docs/app-architecture.md",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
)


def archive(root, bundle, output, *, version):
    root, bundle, output = Path(root).resolve(), Path(bundle).resolve(), Path(output).resolve()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?", version):
        raise ValueError("version must be a release number")
    if output.suffix != ".zip":
        raise ValueError("distribution must be a ZIP")
    verify(bundle)
    files = [(root / name, name) for name in ROOT_FILES]
    for directory, suffixes in (
        ("src", {".py"}),
        ("frontend/src", {".ts", ".tsx", ".css", ".svg"}),
        ("frontend/scripts", {".mjs"}),
        ("frontend/tests", {".mjs"}),
        ("frontend/public", {".svg", ".webmanifest"}),
        ("docs", {".md", ".svg", ".html"}),
    ):
        files.extend(
            (p, str(p.relative_to(root)))
            for p in (root / directory).rglob("*")
            if p.is_file() and p.suffix in suffixes
        )
    files.extend(
        (p, "bundle/" + str(p.relative_to(bundle))) for p in bundle.rglob("*") if p.is_file()
    )
    # Git未管理の追加コードも含めるが、環境・履歴・認証ファイルは広範囲コピーしない。
    files = sorted(set(files), key=lambda row: row[1])
    for source, _ in files:
        if not source.is_file() or source.is_symlink():
            raise ValueError("distribution requires regular files")
        if not (source.resolve().is_relative_to(root) or source.resolve().is_relative_to(bundle)):
            raise ValueError("distribution path escapes source roots")
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"music-recsys-mac-{version}/"
    with output.open("xb") as stream:
        try:
            with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_DEFLATED) as package:
                for source, name in files:
                    target = "MusicRecsys.command" if name == "deploy/MusicRecsys.command" else name
                    info = zipfile.ZipInfo(prefix + target)
                    info.create_system = 3
                    info.external_attr = (
                        0o100755 if target.endswith(".command") else 0o100644
                    ) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    package.writestr(info, source.read_bytes())
                package.writestr(prefix + "VERSION", version + "\n")
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(archive(args.root, args.bundle, args.output, version=args.version))


if __name__ == "__main__":
    main()
