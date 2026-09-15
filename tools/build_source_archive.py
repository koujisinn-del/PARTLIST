"""Build a source-only distribution, using an explicit allowlist, never binaries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BANNED = {".exe", ".dll", ".pyd", ".pyc", ".whl", ".msi", ".ttf", ".ttc", ".shx"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--frame-dir", type=Path, required=True)
    args = parser.parse_args()
    stage = args.stage.resolve()
    if stage.exists():
        raise SystemExit("Use a new staging directory; previous packages will not be overwritten.")
    stage.mkdir(parents=True)
    entries = {}
    root_names = (
        "main.py", "app_launcher.pyw", "launcher_ja.pyw", "setup_source.py", "启动部材表生成器.cmd", "起動.cmd",
        "requirements.txt", "README.md", "项目开发记录.md", "公司电脑源码运行说明.md",
        "テスト用部材データ.xlsx", "テスト用部材データ0904.xlsx",
        "図面テンプレート.dwg", "図面テンプレート_テキスト枠.dwg",
    )
    for name in root_names:
        entries[name] = ROOT / name
    for directory, suffixes in (("material_drawing_generator", {".py"}), ("tests", {".py"}),
                                ("site_templates", {".json"}), ("test_data", {".xlsx", ".json"}), ("material_libraries", {".json", ".md"})):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts:
                entries[path.relative_to(ROOT).as_posix()] = path
    for name in ("build_source_archive.py", "verify_cad_text_output.py", "preview_material_symbols.py"):
        entries["tools/" + name] = ROOT / "tools" / name
    for name in ("示例部材表_可变断面行数.xlsx", "示例部材表_含图纸列表.xlsx"):
        local_fixture = ROOT / "test_data" / name
        entries["test_data/" + name] = (local_fixture if local_fixture.exists() else
            ROOT / "outputs/01a05d12-c37e-7963-95ee-47146f559f0a" / name)
    for name in ("図面テンプレート.dxf", "図面テンプレート_テキスト枠.dxf"):
        entries[name] = args.frame_dir / name
    hashes = {}
    for name, source in sorted(entries.items()):
        if source.suffix.lower() in BANNED:
            raise ValueError(f"Binary not allowed: {source}")
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (stage / "源码文件清单.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, f"{stage.name}/{path.relative_to(stage).as_posix()}")
    with zipfile.ZipFile(args.zip) as archive:
        assert archive.testzip() is None
        assert not any(Path(name).suffix.lower() in BANNED for name in archive.namelist())
    print(f"Source archive: {args.zip.resolve()} ({len(hashes) + 1} files)")


if __name__ == "__main__":
    main()
