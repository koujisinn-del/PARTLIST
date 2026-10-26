from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ezdxf
from ezdxf import bbox, units

from material_drawing_generator.cad_output_scale import (
    CAD_OUTPUT_SCALES, DEFAULT_CAD_OUTPUT_SCALE, scale_document, validate_cad_output_scale,
)
from material_drawing_generator.drawing import _converter_root
from material_drawing_generator.material_drawing import verify_material_cells
from material_drawing_generator.models import SectionRow, SheetData, WorkbookData, group_section_rows
from material_drawing_generator.service import generate_drawings
from material_drawing_generator.settings import UserSettings
from material_drawing_generator.site_template_store import SiteTemplateStore
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.text_pdf import TextPdfBook


ROOT = Path(__file__).resolve().parents[1]


def drawing_fixture():
    doc = ezdxf.new("R2010", setup=False)
    doc.units = units.MM
    doc.header["$LIMMIN"] = (0, 0)
    doc.header["$LIMMAX"] = (420, 297)
    doc.header["$LTSCALE"] = 1.75
    msp = doc.modelspace()
    msp.add_line((10, 20), (30, 40), dxfattribs={"ltscale": 2.5})
    msp.add_circle((50, 60), 4)
    msp.add_text("OUTLINE", dxfattribs={"insert": (70, 80), "height": 2})
    block = doc.blocks.new("SCALE_SYMBOL")
    block.add_line((0, 0), (2, 0))
    block.add_text("B", dxfattribs={"insert": (1, 0), "height": 1})
    msp.add_blockref("SCALE_SYMBOL", (100, 110), dxfattribs={"xscale": 1, "yscale": 1, "zscale": 1})
    return doc


class CadOutputScaleTests(unittest.TestCase):
    def test_only_three_exact_integer_values_are_accepted(self):
        self.assertEqual(CAD_OUTPUT_SCALES, (100, 150, 200))
        self.assertEqual(DEFAULT_CAD_OUTPUT_SCALE, 150)
        for value in CAD_OUTPUT_SCALES:
            self.assertEqual(validate_cad_output_scale(value), value)
        for value in (True, False, 100.0, 150.0, "150", "200", 1, -1, 0, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_cad_output_scale(value)

    def test_each_scale_transforms_modelspace_once_and_keeps_blocks_unit_sized(self):
        source = drawing_fixture()
        original_insert = source.modelspace().query("INSERT").first
        original_block_text = source.blocks["SCALE_SYMBOL"].query("TEXT").first
        for factor in CAD_OUTPUT_SCALES:
            with self.subTest(factor=factor):
                scaled = scale_document(source, factor, 420, 297)
                line = scaled.modelspace().query("LINE").first
                circle = scaled.modelspace().query("CIRCLE").first
                text = next(item for item in scaled.modelspace().query("TEXT") if item.dxf.text == "OUTLINE")
                insert = scaled.modelspace().query("INSERT").first
                self.assertEqual(tuple(line.dxf.start)[:2], (10 * factor, 20 * factor))
                self.assertEqual(tuple(circle.dxf.center)[:2], (50 * factor, 60 * factor))
                self.assertAlmostEqual(circle.dxf.radius, 4 * factor)
                self.assertEqual(tuple(text.dxf.insert)[:2], (70 * factor, 80 * factor))
                self.assertAlmostEqual(text.dxf.height, 2 * factor)
                self.assertEqual(tuple(insert.dxf.insert)[:2], (100 * factor, 110 * factor))
                self.assertEqual((insert.dxf.xscale, insert.dxf.yscale, insert.dxf.zscale), (factor, factor, factor))
                virtual_text = next(item for item in insert.virtual_entities() if item.dxftype() == "TEXT")
                self.assertEqual(virtual_text.dxf.text, "B")
                self.assertAlmostEqual(virtual_text.dxf.height, factor)
                self.assertAlmostEqual(virtual_text.dxf.insert.y, 110 * factor)
                self.assertAlmostEqual(scaled.blocks["SCALE_SYMBOL"].query("TEXT").first.dxf.height, 1)
                self.assertAlmostEqual(scaled.blocks["SCALE_SYMBOL"].query("LINE").first.dxf.end.x, 2)
                self.assertEqual(scaled.units, units.MM)
                self.assertEqual(scaled.header["$INSUNITS"], units.MM)
                self.assertEqual(tuple(scaled.header["$LIMMIN"]), (0, 0))
                self.assertEqual(tuple(scaled.header["$LIMMAX"]), (420 * factor, 297 * factor))
                self.assertAlmostEqual(scaled.header["$LTSCALE"], 1.75 * factor)
                self.assertAlmostEqual(line.dxf.ltscale, 2.5)
                self.assertEqual(source.modelspace().query("INSERT").first.dxf.insert, original_insert.dxf.insert)
                self.assertEqual(source.blocks["SCALE_SYMBOL"].query("TEXT").first.dxf.height, original_block_text.dxf.height)

    def test_untransformable_entity_fails_without_mutating_source(self):
        source = drawing_fixture()
        entity = source.modelspace().query("LINE").first
        entry = SimpleNamespace(entity=entity, message="test unsupported entity")
        with patch("ezdxf.transform.inplace", return_value=[entry]):
            with self.assertRaisesRegex(ValueError, "test unsupported entity"):
                scale_document(source, 150, 420, 297)
        self.assertEqual(tuple(source.modelspace().query("LINE").first.dxf.start)[:2], (10, 20))

    def test_compatibility_dxf_preserves_scaled_linetype_and_model_limits(self):
        from material_drawing_generator.cad_integrity import write_compatibility_dxf, validate_roundtrip
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "scaled.dxf"
            compat_path = Path(directory) / "compat.dxf"
            scale_document(drawing_fixture(), 150, 420, 297).saveas(source_path)
            write_compatibility_dxf(source_path, compat_path)
            source = ezdxf.readfile(source_path)
            compat = ezdxf.readfile(compat_path)
            self.assertAlmostEqual(compat.header["$LTSCALE"], 262.5)
            self.assertAlmostEqual(compat.modelspace().query("LINE").first.dxf.ltscale, 2.5)
            self.assertEqual(tuple(compat.modelspace().dxf.limmax)[:2], (63000, 44550))
            self.assertEqual(tuple(compat.header["$LIMMAX"]), (63000, 44550))
            self.assertEqual(tuple(compat.header["$PLIMMAX"]), tuple(source.header["$PLIMMAX"]))
            validate_roundtrip(compat_path, compat_path)
            from material_drawing_generator.drawing import _converter_root
            converter_root = _converter_root()
            writer = converter_root / "dwgwrite.exe"
            reader = converter_root / "dwg2dxf.exe"
            if writer.exists() and reader.exists():
                dwg_path = Path(directory) / "scaled.dwg"
                reread_path = Path(directory) / "dwg-roundtrip.dxf"
                result = subprocess.run(
                    [str(writer), "-y", "--as", "r2000", "-I", "DXF", "-o", str(dwg_path), str(compat_path)],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run(
                    [str(reader), "-y", "-o", str(reread_path), str(dwg_path)],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                reread = ezdxf.readfile(reread_path)
                self.assertAlmostEqual(reread.header["$LTSCALE"], 262.5)
                self.assertEqual(tuple(reread.header["$LIMMAX"]), (63000, 44550))
                self.assertEqual(tuple(reread.modelspace().dxf.limmax)[:2], (63000, 44550))
                validate_roundtrip(compat_path, reread_path)

    def test_invalid_service_scale_fails_before_excel_or_output_access(self):
        from material_drawing_generator import service
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing-output"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("original", encoding="utf-8")
            with patch("material_drawing_generator.service.read_workbook", side_effect=AssertionError("must not read Excel")) as read, patch(
                "material_drawing_generator.service.apply_submission_highlighting",
                side_effect=AssertionError("must not modify Excel"),
            ):
                for value in (True, 100.0, 150.0, "150", 1, -1, math.nan):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        generate_drawings("missing.xlsx", output, SiteTemplate.load(default_template_path()), {"dxf"}, cad_output_scale=value)
                read.assert_not_called()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")

    def test_service_scales_cad_modes_but_pdf_only_uses_original_a3_geometry(self):
        from material_drawing_generator import service
        from material_drawing_generator.excel_marker import HighlightResult
        template = SiteTemplate.load(default_template_path())
        row = SectionRow(2, "G1", "大梁", position="中央", section="H-100")
        sheet = SheetData("2F", 1, [row], group_section_rows([row], template.category_order))
        workbook = WorkbookData("synthetic.xlsx", [sheet])
        real_scale = service.scale_document
        paper_extents = []
        real_add_page = TextPdfBook.add_cad_page

        def capture_scale(document, factor, width, height):
            bounds = bbox.extents(document.modelspace())
            paper_extents.append((tuple(bounds.extmin), tuple(bounds.extmax)))
            return real_scale(document, factor, width, height)

        def capture_pdf(book, document, name, expected_sections=None):
            bounds = bbox.extents(document.modelspace())
            pdf_extents.append((tuple(bounds.extmin), tuple(bounds.extmax)))
            return real_add_page(book, document, name, expected_sections)

        pdf_extents = []
        with tempfile.TemporaryDirectory() as directory, patch(
            "material_drawing_generator.service.read_workbook", return_value=workbook
        ), patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)), patch(
            "material_drawing_generator.service.scale_document", side_effect=capture_scale
        ), patch.object(TextPdfBook, "add_cad_page", autospec=True, side_effect=capture_pdf):
            outputs = {}
            for formats, factor, label in (({"dxf"}, 100, "dxf"), ({"pdf"}, 200, "pdf")):
                root = Path(directory) / label
                outputs[label] = generate_drawings("synthetic.xlsx", root, template, formats, cad_output_scale=factor)
                generated = outputs[label].sheets[0]
                if label == "dxf":
                    self.assertEqual(generated.files.keys(), {"dxf"})
                    final = ezdxf.readfile(generated.files["dxf"])
                    bounds = bbox.extents(final.modelspace())
                    self.assertAlmostEqual(bounds.extmin.x, paper_extents[-1][0][0] * factor)
                    self.assertAlmostEqual(bounds.extmax.x, paper_extents[-1][1][0] * factor)
                    self.assertEqual(tuple(final.header["$LIMMAX"]), (42000, 29700))
                else:
                    self.assertIn("pdf", generated.files)
                    self.assertNotIn("dxf", generated.files)
                    self.assertFalse((root / "2F.dxf").exists())
                    self.assertFalse((root / "2F.dwg").exists())
                    self.assertEqual(len(pdf_extents), 1)
                    self.assertEqual(pdf_extents[0], paper_extents[0])
                    self.assertEqual(outputs[label].sheets[0].pdf_page, 1)

        if (_converter_root() / "dwgwrite.exe").exists():
            with tempfile.TemporaryDirectory() as directory, patch(
                "material_drawing_generator.service.read_workbook", return_value=workbook
            ), patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)):
                root = Path(directory) / "dwg-only"
                result = generate_drawings("synthetic.xlsx", root, template, {"dwg"}, cad_output_scale=150)
                generated = result.sheets[0]
                self.assertIn("dwg", generated.files)
                self.assertIn("dxf_compatibility_backup", generated.files)
                cad = ezdxf.readfile(generated.files["dxf_compatibility_backup"])
                self.assertEqual(tuple(cad.header["$LIMMAX"]), (63000, 44550))
                self.assertTrue(Path(generated.files["dwg"]).is_file())

    def test_material_continuation_cells_are_verified_in_scaled_cad_coordinates(self):
        from material_drawing_generator import service
        from material_drawing_generator.excel_marker import HighlightResult
        template = SiteTemplate.load(default_template_path())
        template.data["material_symbols"] = {"enabled": True}
        rows = [SectionRow(i + 2, "BeamAb", "大梁", section="H-100", material="TMCP325B") for i in range(100)]
        sheet = SheetData("2F", 1, rows, group_section_rows(rows, template.category_order))
        workbook = WorkbookData("synthetic.xlsx", [sheet])
        template.data["cad_output_scale"] = 150
        with tempfile.TemporaryDirectory() as directory, patch(
            "material_drawing_generator.service.read_workbook", return_value=workbook
        ), patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)):
            result = generate_drawings("synthetic.xlsx", Path(directory) / "output", template, {"pdf", "dxf"})
            generated = result.sheets[0]
            doc = ezdxf.readfile(generated.files["dxf"])
            report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
            cells = report["material_symbols"]["2F"]
            self.assertEqual(cells["cad_output_scale"], 150)
            self.assertIn("final CAD modelspace", cells["coordinate_space"])
            self.assertEqual(cells["paper_check"]["coordinate_space"], "A3 page millimetres before CAD scaling")
            self.assertEqual(cells["verified_rows"], 100)
            self.assertEqual(len(cells["cells"]), 100)
            for cell in cells["cells"]:
                section = doc.entitydb.get(cell["text_handle"])
                insert = doc.entitydb.get(cell["block_handle"])
                self.assertAlmostEqual(section.dxf.height, 150, places=6)
                symbol_texts = [item for item in insert.virtual_entities() if item.dxftype() == "TEXT"]
                self.assertEqual(len(symbol_texts), 2)
                for symbol_text in symbol_texts:
                    self.assertAlmostEqual(symbol_text.dxf.height, section.dxf.height, places=6)
                    self.assertAlmostEqual(symbol_text.dxf.insert.y, section.dxf.insert.y, places=6)
            self.assertEqual(generated.pdf_page, 1)
            self.assertTrue(Path(result.pdf_path).is_file())

    def test_service_default_uses_template_scale_then_150_for_legacy_template(self):
        from material_drawing_generator.excel_marker import HighlightResult
        row = SectionRow(2, "G1", "大梁", section="H-100")
        sheet = SheetData("2F", 1, [row], group_section_rows([row], ["大梁"]))
        workbook = WorkbookData("synthetic.xlsx", [sheet])
        with tempfile.TemporaryDirectory() as directory, patch(
            "material_drawing_generator.service.read_workbook", return_value=workbook
        ), patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)):
            selected_template = SiteTemplate.load(default_template_path())
            selected_template.data["cad_output_scale"] = 200
            selected = generate_drawings("synthetic.xlsx", Path(directory) / "selected", selected_template, {"dxf"})
            selected_doc = ezdxf.readfile(selected.sheets[0].files["dxf"])
            self.assertEqual(tuple(selected_doc.header["$LIMMAX"]), (84000, 59400))
            legacy_template = SiteTemplate.load(default_template_path())
            legacy = generate_drawings("synthetic.xlsx", Path(directory) / "legacy", legacy_template, {"dxf"})
            legacy_doc = ezdxf.readfile(legacy.sheets[0].files["dxf"])
            self.assertEqual(tuple(legacy_doc.header["$LIMMAX"]), (63000, 44550))

    def test_settings_and_named_site_template_persist_scale_and_old_records_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("material_drawing_generator.settings.app_data_root", return_value=root):
                legacy = root / "settings.json"
                legacy.write_text(json.dumps({"excel_path": "old.xlsx"}), encoding="utf-8")
                self.assertEqual(UserSettings.load().cad_output_scale, DEFAULT_CAD_OUTPUT_SCALE)
                current = UserSettings(cad_output_scale=200)
                current.save()
                self.assertEqual(UserSettings.load().cad_output_scale, 200)
            store = SiteTemplateStore("zh", root / "sites.json")
            layout = SiteTemplate.load(default_template_path()).data
            layout["cad_output_scale"] = 200
            store.save("比例测试", layout, {"cad_output_scale": 200})
            saved = SiteTemplateStore("zh", root / "sites.json").get("比例测试")
            self.assertEqual(saved["layout"]["cad_output_scale"], 200)
            self.assertEqual(saved["ui"]["cad_output_scale"], 200)
            old_layout = SiteTemplate.load(default_template_path()).data
            self.assertNotIn("cad_output_scale", old_layout)
            self.assertEqual(DEFAULT_CAD_OUTPUT_SCALE, 150)

    def test_gui_scale_control_is_bilingual_and_site_setting_reloads(self):
        from material_drawing_generator.i18n import translator
        from material_drawing_generator.gui import MaterialDrawingApp
        self.assertEqual(translator("zh")("cad_output_scale"), "CAD输出倍率")
        self.assertIn("PDF", translator("zh")("cad_output_scale_note"))
        self.assertEqual(translator("ja")("cad_output_scale"), "CAD出力倍率")
        self.assertIn("PDF", translator("ja")("cad_output_scale_note"))
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"TEMP": directory, "TMP": directory, "LOCALAPPDATA": directory, "APPDATA": directory},
        ):
            try:
                root = tk.Tk()
                root.withdraw()
            except tk.TclError as exc:
                self.skipTest(f"Tk display unavailable: {exc}")
            try:
                root.geometry("920x700")
                app = MaterialDrawingApp(root, language="zh")
                self.assertEqual(tuple(app.cad_output_scale_combo.cget("values")), ("100", "150", "200"))
                root.update_idletasks()
                scale_row_right = app.cad_output_scale_combo.master.winfo_width()
                for widget in (app.cad_output_scale_label, app.cad_output_scale_combo, app.cad_output_scale_note):
                    self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), scale_row_right)
                app.cad_output_scale_var.set("200")
                app._save_settings()
                app.template_name_var.set("ScaleSite")
                with patch("material_drawing_generator.gui.messagebox.askyesno", return_value=True):
                    app._save_site_template()
                record = app.template_store.get("ScaleSite")
                self.assertEqual(record["ui"]["cad_output_scale"], 200)
                self.assertEqual(record["layout"]["cad_output_scale"], 200)
                app.cad_output_scale_var.set("100")
                app._load_site_template()
                self.assertEqual(app.cad_output_scale_var.get(), "200")
                self.assertEqual(UserSettings.load().cad_output_scale, 200)
            finally:
                root.destroy()
            root = tk.Tk()
            root.withdraw()
            try:
                root.geometry("920x700")
                app = MaterialDrawingApp(root, language="ja")
                root.update_idletasks()
                self.assertEqual(app.cad_output_scale_label.cget("text"), "CAD出力倍率")
                scale_row_right = app.cad_output_scale_combo.master.winfo_width()
                for widget in (app.cad_output_scale_label, app.cad_output_scale_combo, app.cad_output_scale_note):
                    self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), scale_row_right)
                self.assertIn("A3", app.cad_output_scale_note.cget("text"))
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
