from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RELEASE = ROOT / "release"
BUILD = ROOT / "build" / "pyinstaller"
SPECS = ROOT / "build" / "specs"

LIBREDWG_FILES = (
    "dwg2dxf.exe",
    "dwgwrite.exe",
    "libredwg-0.dll",
    "libiconv-2.dll",
    "libpcre2-16-0.dll",
    "libpcre2-8-0.dll",
)

PACKAGES = (
    {
        "name": "部材表图纸生成器_中文版",
        "folder": "中文版",
        "launcher": "app_launcher.pyw",
        "guide": ROOT / "docs" / "使用说明_中文版.txt",
    },
    {
        "name": "部材表図面生成ツール_日本語版",
        "folder": "日本語版",
        "launcher": "launcher_ja.pyw",
        "guide": ROOT / "docs" / "取扱説明_日本語版.txt",
    },
)


def ensure_workspace_path(path: Path) -> None:
    resolved = path.resolve()
    if ROOT not in resolved.parents and resolved != ROOT:
        raise RuntimeError(f"Refusing to modify a path outside the project: {resolved}")


def remove_existing(path: Path) -> None:
    ensure_workspace_path(path)
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def build_package(package: dict[str, object]) -> None:
    name = str(package["name"])
    localized_root = RELEASE / str(package["folder"])
    package_dir = localized_root / name
    remove_existing(package_dir)
    remove_existing(BUILD / name)

    arguments = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--noupx",
        "--name",
        name,
        "--distpath",
        str(localized_root),
        "--workpath",
        str(BUILD / name),
        "--specpath",
        str(SPECS),
        "--add-data",
        f"{ROOT / 'site_templates'}{os.pathsep}site_templates",
    ]
    for filename in LIBREDWG_FILES:
        arguments.extend(
            [
                "--add-binary",
                f"{ROOT / 'libredwg-0.14-win64' / filename}{os.pathsep}libredwg-0.14-win64",
            ]
        )
    arguments.append(str(ROOT / str(package["launcher"])))
    subprocess.run(arguments, cwd=ROOT, check=True)

    shutil.copy2(Path(package["guide"]), package_dir / Path(package["guide"]).name)

    sample_dir = package_dir / ("サンプル" if str(package["folder"]) == "日本語版" else "示例文件")
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_workbook = (
        ROOT
        / "outputs"
        / "01a05d12-c37e-7963-95ee-47146f559f0a"
        / "示例部材表_含图纸列表.xlsx"
    )
    if sample_workbook.exists():
        shutil.copy2(sample_workbook, sample_dir / sample_workbook.name)

    archive_base = RELEASE / f"{name}_便携包"
    if str(package["folder"]) == "日本語版":
        archive_base = RELEASE / f"{name}_ポータブル"
    remove_existing(archive_base.with_suffix(".zip"))
    shutil.make_archive(str(archive_base), "zip", root_dir=localized_root, base_dir=name)


def main() -> None:
    RELEASE.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    SPECS.mkdir(parents=True, exist_ok=True)
    for package in PACKAGES:
        build_package(package)
    print("Portable packages created in:", RELEASE)


if __name__ == "__main__":
    main()
