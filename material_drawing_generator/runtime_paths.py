from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


APP_DATA_FOLDER = "MaterialDrawingGenerator"


def resource_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[1]


def dwg_tools_available() -> bool:
    base = resource_root()
    for name in ("libredwg-0.14.8594-win64", "libredwg-0.14-win64"):
        if all((base / name / file).is_file() for file in ("dwgwrite.exe", "dwg2dxf.exe", "dwgread.exe")):
            return True
    return False


def app_data_root() -> Path:
    app_folder = APP_DATA_FOLDER
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        root = Path(base) / app_folder
    else:
        root = Path.home() / f".{app_folder}"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        root = Path(tempfile.gettempdir()) / app_folder
        root.mkdir(parents=True, exist_ok=True)
    return root
