"""Generate a reviewable CAD/PDF legend from the exact installed rule files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from material_drawing_generator.drawing import CadAdapter, _cjk_font_filename, convert_dxf_to_dwg
from material_drawing_generator.materials import MaterialLibrary
from material_drawing_generator.material_drawing import SymbolFactory, SheetMaterials, MaterialBinding, verify_material_cells
from material_drawing_generator.models import SectionRow
from material_drawing_generator.text_pdf import TextPdfBook


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dwg", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    library = MaterialLibrary.load()
    factory = SymbolFactory(_cjk_font_filename())
    book = TextPdfBook(args.output / "材质符号对照样张.pdf", 420, 297)
    pages = []
    page_size = 16
    for start in range(0, len(library.rules), page_size):
        rules = library.rules[start:start + page_size]
        page = start // page_size + 1
        basename = f"材质符号对照_{page}"
        dxf_path = args.output / f"{basename}.dxf"
        adapter = CadAdapter(dxf_path, 420, 297)
        adapter.strict_table_text = True
        adapter.rect(10, 10, 410, 287, "BORDER")
        adapter.text_box(20, 271, 400, 282, "基础材质符号库 / 基本材質記号", "TEXT", 4)
        adapter.text_box(20, 257, 400, 267, "记号依据提供的图例；断面文字仅作位置示意。字母可编辑，外框为矢量。", "TEXT", 2.5)
        top, step = 245, 10
        bottom = top - (len(rules) + 1) * step
        adapter.rect(25, bottom, 395, top, "BORDER")
        adapter.line(105, bottom, 105, top, "BORDER")
        adapter.line(330, bottom, 330, top, "BORDER")
        for index in range(1, len(rules) + 1):
            y = top - index * step
            adapter.line(25, y, 395, y, "BORDER")
        adapter.text_box(25, top - step, 105, top, "Excel 材质全称", "TEXT", 3)
        adapter.text_box(105, top - step, 330, top, "CAD / PDF 实际输出", "TEXT", 3)
        adapter.text_box(330, top - step, 395, top, "外框 / 框内文字", "TEXT", 3)
        shapes = {"none": "无记号", "circle": "圆", "square": "方框", "triangle": "三角", "diamond": "菱形", "pentagon": "五边形", "hexagon": "六边形", "star": "星形"}
        rows = [SectionRow(i + 2, rule.material, "大梁", section="断面寸法", material=rule.material) for i, rule in enumerate(rules)]
        plan = SheetMaterials(basename, {row.source_row: MaterialBinding(row.source_row, row.material, row.section, rule) for row, rule in zip(rows, rules)}, library.fingerprint, factory)
        for i, (row, rule) in enumerate(zip(rows, rules)):
            y2 = top - (i + 1) * step
            y1 = y2 - step
            adapter.text_box(25, y1, 105, y2, rule.material, "TEXT", 3)
            factory.draw_cell(adapter, plan, row, 105, y1, 330, y2, 3)
            adapter.text_box(330, y1, 395, y2, shapes[rule.shape] + (f" / {rule.inner}" if rule.inner else ""), "TEXT", 3)
        adapter.text_box(20, 45, 400, 54, "自定义：提供材质名称及正确记号后，追加到 custom.json；重复名称会提示。", "TEXT", 2.5)
        adapter.text_box(20, 32, 400, 41, "材质符号试验版 · 对照确认用 · 不是工程设计图", "TEXT", 2.5)
        adapter.save()
        doc = ezdxf.readfile(dxf_path)
        checks = verify_material_cells(doc, plan)
        if args.dwg:
            convert_dxf_to_dwg(dxf_path, args.output / f"{basename}.dwg")
        book.add_cad_page(doc, basename)
        pages.append(checks)
    book.save()
    (args.output / "材质核验.json").write_text(json.dumps({"pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(library.rules)} materials / {len(pages)} pages: {args.output.resolve()}")


if __name__ == "__main__":
    main()
