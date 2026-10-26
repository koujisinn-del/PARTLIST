"""Create editable, flat source examples for importing custom material symbols."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from ezdxf import bbox

from material_drawing_generator.drawing import CadAdapter, _cjk_font_filename, convert_dxf_to_dwg
from material_drawing_generator.material_drawing import MaterialBinding, SheetMaterials, SymbolFactory, verify_material_cells
from material_drawing_generator.material_preview import symbol_section_preview_png
from material_drawing_generator.material_symbol_import import build_rule, inspect_drawing
from material_drawing_generator.materials import SymbolRule
from material_drawing_generator.models import SectionRow

OUT = ROOT / "docs" / "material_symbol_sources"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = ROOT / ".test-temp" / "material-symbol-sources"
    temporary.mkdir(parents=True, exist_ok=True)
    protected = [ROOT / "material_libraries" / name for name in ("base.json", "custom.json")]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in protected]
    font = _cjk_font_filename()
    factory = SymbolFactory(font)
    checks = []
    for shape, letter in (("circle", "N"), ("pentagon", "T"), ("hexagon", "N")):
        stem = f"{shape}_{letter}"
        original = SymbolRule(f"QA_ONLY_{stem}", shape=shape, inner=letter)
        geometry = factory.geometry(original)
        # These are drawing examples, not engineering-grade mappings. Keep
        # the editable letter at height 100 and set an example outline height
        # of 165 so the imported result fits the default 2.5 mm row.
        xs = [value for line in geometry.lines for value in (line[0], line[2])]
        ys = [value for line in geometry.lines for value in (line[1], line[3])]
        for x, y, radius in geometry.circles:
            xs.extend((x - radius, x + radius))
            ys.extend((y - radius, y + radius))
        center_x, center_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        outline_factor = 1.65 / (max(ys) - min(ys))

        def point(x, y):
            return ((center_x + (x - center_x) * outline_factor) * 100,
                    (center_y + (y - center_y) * outline_factor) * 100)

        dxf = OUT / f"{stem}.dxf"
        adapter = CadAdapter(dxf, 420, 297)
        for label, x, y in geometry.texts:
            adapter.modelspace.add_text(label, dxfattribs={
                "insert": (x * 100, y * 100), "height": 100,
                "style": adapter.text_style, "layer": "TEXT",
            })
        for x1, y1, x2, y2 in geometry.lines:
            adapter.line(*point(x1, y1), *point(x2, y2), "BORDER")
        for x, y, radius in geometry.circles:
            adapter.circle(*point(x, y), radius * 100 * outline_factor, "BORDER")
        limits = bbox.extents(adapter.modelspace)
        adapter.modelspace.dxf.limmin = (limits.extmin.x - 20, limits.extmin.y - 20)
        adapter.modelspace.dxf.limmax = (limits.extmax.x + 20, limits.extmax.y + 20)
        adapter.save()
        dwg = OUT / f"{stem}.dwg"
        convert_dxf_to_dwg(dxf, dwg)
        for source in (dxf, dwg):
            raw, _circles = inspect_drawing(source, font)
            rule = build_rule(original.material, (), raw, "text", font_filename=font, text_index=0)
            assert len([item for item in raw if item[0] == "TEXT"]) == 1
            assert rule.baseline_text_index == 0
            assert next(item for item in rule.geometry if item[0] == "TEXT")[4] == 1.0
            cell_adapter = CadAdapter(temporary / f"{source.suffix[1:]}_{stem}.dxf", 420, 297)
            row = SectionRow(2, "QA_ONLY", "大梁", section="H-300X150X6.5X9", material=rule.material)
            plan = SheetMaterials(stem, {2: MaterialBinding(2, rule.material, row.section, rule)}, "temporary-qa", SymbolFactory(font))
            plan.factory.draw_cell(cell_adapter, plan, row, 0, 0, 60, 2.5, 1.0)
            cell_adapter.save()
            assert verify_material_cells(ezdxf.readfile(cell_adapter.output_path), plan)["verified_rows"] == 1
        (OUT / f"{stem}.png").write_bytes(symbol_section_preview_png(rule, font))
        checks.append({"example": stem, "source_text_height": 100, "normalized_text_height": 1, "editable_texts": 1, "dxf_and_dwg_import": "passed", "default_row_fit": "passed"})
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in protected]
    (OUT / "validation.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps(checks))


if __name__ == "__main__":
    main()
