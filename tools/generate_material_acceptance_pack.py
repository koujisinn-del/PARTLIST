"""Build a two-page, CAD-backed material symbol acceptance pack."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf

from material_drawing_generator.drawing import CadAdapter, _cjk_font_filename, _converter_root, convert_dxf_to_dwg
from material_drawing_generator.material_drawing import MaterialBinding, SheetMaterials, SymbolFactory, verify_material_cells
from material_drawing_generator.material_symbol_import import build_rule
from material_drawing_generator.materials import MaterialLibrary, SymbolRule
from material_drawing_generator.models import SectionRow
from material_drawing_generator.text_pdf import TextPdfBook


OUT = ROOT / "output" / "pdf" / "material_acceptance_20261026"
TEMP = ROOT / ".test-temp" / "acceptance-dev"
SECTION = "H-300X150X6.5X9"
SIZE = 2.8
PAPER = (420.0, 297.0)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rules() -> tuple[SymbolRule, ...]:
    rules = [
        SymbolRule("QA_ONLY_CIRCLE_NB", prefix="N", inner="B", shape="circle"),
        SymbolRule("QA_ONLY_SQUARE_TB", prefix="T", inner="B", shape="square"),
        SymbolRule("QA_ONLY_DIAMOND_T", prefix="T", shape="diamond"),
        SymbolRule("QA_ONLY_PENTAGON_B", inner="B", shape="pentagon"),
        SymbolRule("QA_ONLY_HEXAGON_N", prefix="N", shape="hexagon"),
        SymbolRule("QA_ONLY_TRIANGLE_N", prefix="N", shape="triangle"),
        SymbolRule("QA_ONLY_STAR_T", prefix="T", shape="star"),
    ]
    # A temporary imported square and editable source TEXT with a selected
    # baseline anchor. This is deliberately not appended to custom.json.
    raw = [
        ("LINE", 0.0, 0.0, 5.0, 0.0),
        ("LINE", 5.0, 0.0, 5.0, 5.0),
        ("LINE", 5.0, 5.0, 0.0, 5.0),
        ("LINE", 0.0, 5.0, 0.0, 0.0),
        ("TEXT", "Q", 1.8, 1.35, 2.2, 0.0, 1.0),
    ]
    imported = build_rule("QA_ONLY_IMPORTED_BASELINE", (), raw, "text", font_filename=_cjk_font_filename(), text_index=0)
    return (*rules, imported)


def add_label(adapter, text: str, x1: float, y1: float, x2: float, y2: float, size: float = 2.8):
    entity = adapter.modelspace.add_text(text, dxfattribs={
        "insert": ((x1 + x2) / 2, (y1 + y2) / 2), "height": size,
        "style": adapter.text_style, "layer": "TEXT", "halign": 1, "valign": 2,
    })
    entity.dxf.align_point = ((x1 + x2) / 2, (y1 + y2) / 2)


def make_page(path: Path, page_name: str, rules: tuple[SymbolRule, ...], note: str, row_step: float):
    adapter = CadAdapter(path, *PAPER)
    adapter.rect(10, 10, 410, 287, "BORDER")
    add_label(adapter, page_name, 18, 265, 402, 282, 4.0)
    add_label(adapter, note, 18, 254, 402, 264, 2.8)
    top = 247.0
    header_bottom = top - 9.0
    adapter.rect(16, header_bottom - len(rules) * row_step, 404, top, "BORDER")
    columns = (16.0, 96.0, 334.0, 404.0)
    for x in columns[1:-1]:
        adapter.line(x, header_bottom - len(rules) * row_step, x, top, "BORDER")
    for index in range(len(rules) + 1):
        y = header_bottom - index * row_step
        adapter.line(16, y, 404, y, "BORDER")
    add_label(adapter, "材质名称 / 测试标识", columns[0], header_bottom, columns[1], top, 2.8)
    add_label(adapter, "符号 + 断面（CAD 实际输出）", columns[1], header_bottom, columns[2], top, 2.8)
    add_label(adapter, "外框类型", columns[2], header_bottom, columns[3], top, 2.8)

    factory = SymbolFactory(_cjk_font_filename())
    bindings, rows = {}, []
    for index, rule in enumerate(rules):
        source_row = index + 2
        marker = "仅排布试验 / 非工程材质" if rule.material.startswith("QA_ONLY_") else rule.material
        row = SectionRow(source_row, marker, "大梁", section=SECTION, material=rule.material)
        rows.append(row)
        bindings[source_row] = MaterialBinding(source_row, rule.material, SECTION, rule)
        y2 = header_bottom - index * row_step
        y1 = y2 - row_step
        add_label(adapter, marker, columns[0] + 1, y1, columns[1] - 1, y2, 2.8)
        shape_label = {
            "none": "无框", "circle": "圆", "square": "方框", "diamond": "菱形",
            "pentagon": "五角形", "hexagon": "六角形", "triangle": "三角形", "star": "星形",
        }[rule.shape]
        if rule.material == "QA_ONLY_IMPORTED_BASELINE":
            shape_label = "导入方框"
        add_label(adapter, shape_label, columns[2], y1, columns[3], y2, 2.8)
    plan = SheetMaterials(page_name, bindings, "temporary-acceptance-rules", factory)
    for index, row in enumerate(rows):
        y2 = header_bottom - index * row_step
        y1 = y2 - row_step
        factory.draw_cell(adapter, plan, row, columns[1], y1, columns[2], y2, SIZE)
    adapter.save()
    doc = ezdxf.readfile(path)
    checks = verify_material_cells(doc, plan)
    # Confirm native block TEXT is editable and shares exact insertion baseline
    # and height with the section TEXT, including the imported anchor example.
    for cell in plan.cells:
        section = doc.entitydb.get(cell["text_handle"])
        if not cell["block"]:
            continue
        insert = doc.entitydb.get(cell["block_handle"])
        block_texts = [e for e in insert.virtual_entities() if e.dxftype() == "TEXT"]
        for symbol_text in block_texts:
            if abs(float(symbol_text.dxf.insert.y) - float(section.dxf.insert.y)) > 1e-6:
                raise AssertionError(f"{cell['material']} 符号 TEXT 与断面基线不一致")
            if abs(float(symbol_text.dxf.height) - float(section.dxf.height)) > 1e-6:
                raise AssertionError(f"{cell['material']} 符号 TEXT 与断面字号不一致")
    return doc, plan, checks


def generate_cad():
    OUT.mkdir(parents=True, exist_ok=True)
    TEMP.mkdir(parents=True, exist_ok=True)
    base_path = ROOT / "material_libraries" / "base.json"
    custom_path = ROOT / "material_libraries" / "custom.json"
    before = {"base": digest(base_path), "custom": digest(custom_path)}
    base = MaterialLibrary.load().rules
    test = test_rules()
    pages = [
        ("page1_base_materials", "基础材质符号 / 实际字形与断面对照", tuple(base), "16 种内置基础规则 · 断面：" + SECTION, 11.4),
        ("page2_layout_only", "材质符号排布试验（QA ONLY）", test, "仅排布试验非工程材质；名称不代表工程材质 · 断面：" + SECTION, 20.5),
    ]
    documents = []
    for stem, title, rules, note, step in pages:
        dxf = OUT / f"{stem}.dxf"
        doc, plan, checks = make_page(dxf, title, rules, note, step)
        dwg = OUT / f"{stem}.dwg"
        convert_dxf_to_dwg(dxf, dwg)
        # convert_dxf_to_dwg has already performed the application's native
        # DWG -> DXF round trip and strict editable-text/geometry comparison.
        documents.append((doc, title, [SECTION] * len(rules)))
        print(f"{dxf.name}: verify_material_cells={checks['verified_rows']}, DWG round-trip=passed")
    after = {"base": digest(base_path), "custom": digest(custom_path)}
    if after != before:
        raise AssertionError(f"基础/自定义规则库发生意外修改：before={before}, after={after}")
    (OUT / "README.md").write_text(
        """# 材质符号现场验收样张包

请分别打开两页 PDF，并在 DWG 中检查对应 DXF / DWG：

- 符号字母与断面文字字高一致，基线对齐；文字可选中编辑。
- 内置外框围住框内字，整体仍保持几何比例；检查圆、方框、菱形、五角形、六角形、三角形和星形。
- 第 2 页为 QA ONLY 排布试验，全部名称均非工程材质。
- 本工具生成纸面毫米尺度的底层排版样张；主程序支持 100 / 150 / 200 输出倍率，默认 150，PDF 保持 A3。
- 此样张用于现场验收讨论，不能替代 AutoCAD LT 2024 实际打开检查。
""", encoding="utf-8")
    print(f"保护检查通过：base.json={after['base']} custom.json={after['custom']}")


def generate_pdf():
    book = TextPdfBook(OUT / "material_acceptance_20261026.pdf", *PAPER)
    for stem, title in (("page1_base_materials", "基础材质符号"), ("page2_layout_only", "QA ONLY 排布试验")):
        doc = ezdxf.readfile(OUT / f"{stem}.dxf")
        book.add_cad_page(doc, title, expected_sections=[SECTION])
    book.save()
    print(f"PDF created: {(OUT / 'material_acceptance_20261026.pdf').resolve()} pages=2")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-only", action="store_true")
    args = parser.parse_args()
    generate_pdf() if args.pdf_only else generate_cad()
