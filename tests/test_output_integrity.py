from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

import ezdxf
from ezdxf import bbox

from material_drawing_generator.cad_integrity import (
    write_compatibility_dxf, validate_raw_handles, validate_roundtrip,
)
from material_drawing_generator.drawing import (
    CadAdapter, DrawingError, DrawingMetadata, _prepare_tables, draw_sheet,
    convert_dxf_to_dwg,
    _converter_root,
)
from material_drawing_generator.models import SectionRow, SheetData, group_section_rows
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.service import generate_drawings
from material_drawing_generator.text_pdf import TextPdfBook
from material_drawing_generator.xlsx_reader import read_workbook

ROOT = Path(__file__).resolve().parents[1]


def sheet_with(rows):
    return SheetData("2Fリスト", 1, rows, group_section_rows(rows, ["大梁", "小梁"]))


class OutputIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.template = SiteTemplate.load(default_template_path())

    def test_long_text_widens_column_without_changing_font(self):
        small = sheet_with([SectionRow(2, "G1", "大梁", position="中央", section="H-100")])
        large = sheet_with([SectionRow(2, "G1", "大梁", position="軸ブレース補強", section="H-100")])
        a = _prepare_tables(small, self.template)[0]
        b = _prepare_tables(large, self.template)[0]
        self.assertEqual(a[2], b[2])
        self.assertGreater(b[0]["columns"][1]["width"], a[0]["columns"][1]["width"])

    def test_names_are_case_sensitive(self):
        rows = [SectionRow(i + 2, name, "小梁") for i, name in enumerate(["sb24a", "sb24A", "sb24a"])]
        groups = group_section_rows(rows, ["小梁"])
        self.assertEqual({g.member_name: len(g.rows) for g in groups}, {"sb24a": 2, "sb24A": 1})

    def test_position_ink_center_survives_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            src, dst = Path(directory) / "a.dxf", Path(directory) / "b.dxf"
            adapter = CadAdapter(src, 420, 297)
            adapter.strict_table_text = True
            adapter.center_position_ink = True
            for i, text in enumerate(["全断面", "端部", "軸ブレース補強", "X06端"]):
                adapter.text_box(0, i * 2.5, 30, (i + 1) * 2.5, text, "TEXT", 1)
            adapter.save()
            write_compatibility_dxf(src, dst)
            for path in (src, dst):
                doc = ezdxf.readfile(path)
                for i, entity in enumerate(doc.modelspace().query("TEXT")):
                    center = bbox.extents([entity]).center
                    self.assertAlmostEqual(center.x, 15, places=5)
                    self.assertAlmostEqual(center.y, i * 2.5 + 1.25, places=5)

    def test_distant_template_camera_is_reset(self):
        from material_drawing_generator.cad_integrity import reset_model_view
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (420, 297))
        vp = doc.viewports.get_config("*Active")[0]
        vp.dxf.target = (90000, 40000, 0)
        vp.dxf.height = 22000
        reset_model_view(doc)
        vp = doc.viewports.get_config("*Active")[0]
        self.assertEqual(vp.dxf.target, (0, 0, 0))
        self.assertAlmostEqual(vp.dxf.center.x, 210)
        self.assertLess(vp.dxf.height, 1000)

    def test_fixed_grid_across_categories_and_overflow(self):
        rows = [SectionRow(i + 2, f"G{i}", "大梁", section="H-100") for i in range(83)]
        rows.append(SectionRow(90, "B1", "小梁", section="H-100"))
        tables = _prepare_tables(sheet_with(rows), self.template)
        self.assertEqual({t["row_height"] for t, _, _ in tables}, {2.5})
        self.assertEqual({size for _, _, size in tables}, {1.0})
        self.assertEqual(tables[0][0]["title"], "大梁部材リスト")
        rows.extend(SectionRow(i + 100, f"X{i}", "大梁") for i in range(10))
        with self.assertRaisesRegex(DrawingError, "无法放入"):
            _prepare_tables(sheet_with(rows), self.template)

    def test_font_and_grid_are_uniform_with_five_row_merge(self):
        rows = [SectionRow(i + 2, "G1", "大梁", position=f"端部{i}", section="H-100") for i in range(5)]
        rows.append(SectionRow(7, "G2", "大梁", position="軸ブレース補強", section="H-200"))
        sheet = sheet_with(rows)
        captures = []

        class Capture:
            def line(self, *args): pass
            def circle(self, *args): pass
            def rect(self, *args): pass
            def text_box(self, x1, y1, x2, y2, value, layer, size=2.5, *args):
                if getattr(self, "strict_table_text", False):
                    captures.append((str(value), size, y2 - y1))

        draw_sheet(Capture(), sheet, self.template, DrawingMetadata())
        self.assertEqual(len({size for _, size, _ in captures}), 1)
        unit = next(height for value, _, height in captures if value == "部件")
        self.assertAlmostEqual(next(h for value, _, h in captures if value == "G1"), unit * 5)
        self.assertTrue(all(abs(h - unit) < 1e-8 for value, _, h in captures if value != "G1"))

    def test_impossible_width_is_not_hidden_by_shrinking(self):
        sheet = sheet_with([SectionRow(2, "G1", "大梁", section="梁" * 1000)])
        with self.assertRaisesRegex(DrawingError, "不会单独缩小"):
            _prepare_tables(sheet, self.template)

    def test_actual_cells_stay_inside_with_one_font_per_table(self):
        workbook = read_workbook(ROOT / "テスト用部材データ0904.xlsx", self.template)
        with tempfile.TemporaryDirectory() as directory:
            for sheet in workbook.sheets:
                adapter = CadAdapter(Path(directory) / "test.dxf", 420, 297)
                draw_sheet(adapter, sheet, self.template, DrawingMetadata())
                for table, groups, size in _prepare_tables(sheet, self.template):
                    left = table["x"]
                    right = left + sum(c["width"] for c in table["columns"])
                    texts = [e for e in adapter.modelspace.query("TEXT")
                             if left <= e.dxf.insert.x <= right and 60 < e.dxf.insert.y < 290]
                    self.assertTrue(texts)
                    self.assertEqual({round(e.dxf.height, 8) for e in texts}, {round(size, 8)})

    def test_compatibility_unicode_and_plotstyle_references(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "source.dxf", Path(directory) / "compat.dxf"
            doc = ezdxf.new("R2007")
            doc.layers.new("大梁", dxfattribs={"plotstyle_handle": "F"})
            doc.modelspace().add_text("中文材质・日本語・□×", dxfattribs={"layer": "大梁"})
            doc.saveas(a)
            write_compatibility_dxf(a, b)
            validate_raw_handles(b)
            result = ezdxf.readfile(b)
            self.assertEqual(result.encoding, "cp932")
            self.assertEqual(result.modelspace().block_record_handle, "1F")
            self.assertNotIn("B", result.entitydb)
            for layer in result.layers:
                self.assertEqual(result.entitydb[layer.dxf.plotstyle_handle].dxftype(), "ACDBPLACEHOLDER")
            self.assertNotIn("?", result.modelspace().query("TEXT").first.dxf.text)

    def test_duplicate_handles_are_rejected_before_reader_repairs_them(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "duplicate.dxf"
            doc = ezdxf.new("R2000")
            first = doc.modelspace().add_text("A")
            second = doc.modelspace().add_text("B")
            h1, h2 = first.dxf.handle, second.dxf.handle
            doc.saveas(p)
            text = p.read_text(encoding="cp1252")
            text = text.replace("  5\n" + h2 + "\n", "  5\n" + h1 + "\n")
            p.write_text(text, encoding="cp1252")
            with self.assertRaisesRegex(ValueError, "重复"):
                validate_raw_handles(p)

    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").is_file(), "Optional approved DWG converter is not installed")
    def test_converter_error_even_with_zero_exit_keeps_previous_dwg(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "a.dxf"
            output = Path(directory) / "a.dwg"
            ezdxf.new("R2007").saveas(source)
            output.write_bytes(b"PREVIOUS USER FILE")
            with patch("material_drawing_generator.drawing.subprocess.run", return_value=CompletedProcess([], 0, "SUCCESS", "ERROR: Duplicate handle")):
                with self.assertRaisesRegex(DrawingError, "未通过检查"):
                    convert_dxf_to_dwg(source, output)
            self.assertEqual(output.read_bytes(), b"PREVIOUS USER FILE")

    def test_native_pdf_embeds_font_and_never_calls_text_outline_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.pdf"
            adapter = CadAdapter(Path(directory) / "text.dxf", 420, 297)
            adapter.text_box(10, 10, 100, 20, "図面名称 中文", "TEXT", 2)
            from ezdxf.addons.drawing.pipeline import RenderPipeline2d
            with patch.object(RenderPipeline2d, "draw_text", side_effect=AssertionError("text outlines forbidden")):
                book = TextPdfBook(path, 420, 297)
                book.add_cad_page(adapter.doc, "test")
                book.save()
            data = path.read_bytes()
            self.assertIn(b"/ToUnicode", data)
            self.assertIn(b"/Subtype /TrueType", data)

    def test_generation_produces_one_pdf_including_list(self):
        from material_drawing_generator.xlsx_reader import read_workbook
        workbook = read_workbook(ROOT / "テスト用部材データ0904.xlsx", self.template)
        # This test verifies PDF aggregation, not support for the legacy
        # enclosed-B glyph. Use renderable test sections without editing XLSX.
        for sheet in workbook.sheets:
            for row in sheet.source_rows:
                row.section = "[-100X50X5X7.5"
        with tempfile.TemporaryDirectory() as directory:
            with patch("material_drawing_generator.service.read_workbook", return_value=workbook):
                result = generate_drawings(ROOT / "テスト用部材データ0904.xlsx", directory, self.template, {"pdf"})
            self.assertEqual(len(list(Path(directory).glob("*.pdf"))), 1)
            self.assertEqual([s.pdf_page for s in result.sheets], [2, 3])
            self.assertEqual({s.files["pdf"] for s in result.sheets}, {result.pdf_path})

    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").is_file(), "Optional approved DWG converter is not installed")
    def test_new_dwg_converter_roundtrip_keeps_nested_editable_text(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "a.dxf", Path(directory) / "a.dwg"
            adapter = CadAdapter(source, 420, 297)
            block = adapter.doc.blocks.new("テスト")
            block.add_text("日本語 中文", dxfattribs={"style": adapter.text_style})
            block.add_line((0, 0), (10, 10))
            adapter.modelspace.add_blockref("テスト", (10, 10))
            adapter.text_box(10, 30, 90, 40, "大梁 G1", "TEXT", 2)
            adapter.save()
            self.assertEqual(convert_dxf_to_dwg(source, output), [])
            self.assertTrue(output.read_bytes().startswith(b"AC1015"))


if __name__ == "__main__":
    unittest.main()
