"""Regenerate the self-built, fictitious A3 frame example and its DWG copy."""
from pathlib import Path
import sys

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from material_drawing_generator.drawing import _cjk_font_filename, convert_dxf_to_dwg


DESTINATION = ROOT / "docs" / "frame_template_example"
DXF_PATH = DESTINATION / "frame_template_example_150x.dxf"
DWG_PATH = DESTINATION / "frame_template_example_150x.dwg"


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2007")
    document.units = ezdxf.units.MM
    document.header["$LIMMIN"] = (0, 0)
    document.header["$LIMMAX"] = (63000, 44550)
    document.layers.add("FRAME", color=7)
    document.layers.add("FRAME_TEXT", color=7)
    font = _cjk_font_filename()
    style = document.styles.new("MDG_CJK", dxfattribs={"font": font})
    family = {
        "notosanssc-vf.ttf": "Noto Sans SC", "msyh.ttc": "Microsoft YaHei",
        "yugothm.ttc": "Yu Gothic", "meiryo.ttc": "Meiryo",
    }.get(font.lower(), "")
    if family:
        style.set_xdata("ACAD", [(1000, family), (1071, 0)])

    modelspace = document.modelspace()
    # A3 landscape source at 150 model units per paper millimetre.
    modelspace.add_lwpolyline(
        [(0, 0), (63000, 0), (63000, 44550), (0, 44550)],
        close=True, dxfattribs={"layer": "FRAME", "lineweight": 35},
    )
    modelspace.add_lwpolyline(
        [(1200, 1200), (61800, 1200), (61800, 43350), (1200, 43350)],
        close=True, dxfattribs={"layer": "FRAME", "lineweight": 18},
    )
    # Title strip stays below the dynamic table region; only its labels/fields
    # are drawn here, never a fixed part-list table.
    for x in (18000, 30000, 42000, 51000, 58500):
        modelspace.add_line((x, 3000), (x, 7500), dxfattribs={"layer": "FRAME"})
    modelspace.add_line((1200, 3000), (61800, 3000), dxfattribs={"layer": "FRAME"})
    modelspace.add_line((1200, 7500), (61800, 7500), dxfattribs={"layer": "FRAME"})

    def add_text(value: str, x: float, y: float, height: float = 360) -> None:
        modelspace.add_text(value, dxfattribs={
            "insert": (x, y), "height": height, "style": "MDG_CJK", "layer": "FRAME_TEXT",
        })

    # Static headings are intentionally distinct from replacement markers.
    add_text("DRAWING NAME", 1800, 6900, 300)
    add_text("DRAWING NUMBER", 18300, 6900, 300)
    add_text("REVISION", 30300, 6900, 300)
    add_text("CREATED", 42300, 6900, 300)
    add_text("HISTORY", 58800, 6900, 300)
    add_text("図面名称", 1800, 3900)
    add_text("図面番号", 18300, 3900)
    add_text("R-X", 30300, 3900)
    add_text("作成日", 42300, 3900)
    add_text("日付", 58800, 3900)
    document.saveas(DXF_PATH)
    convert_dxf_to_dwg(DXF_PATH, DWG_PATH)
    print(DXF_PATH)
    print(DWG_PATH)


if __name__ == "__main__":
    main()
