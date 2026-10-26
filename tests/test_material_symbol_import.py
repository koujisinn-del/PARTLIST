import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import ezdxf

from material_drawing_generator.drawing import _cjk_font_filename
from material_drawing_generator.material_drawing import SymbolFactory, material_geometry_signature
from material_drawing_generator.material_preview import symbol_preview_png
from material_drawing_generator.material_symbol_import import (
    append_custom_rule, build_rule, inspect_drawing, STANDARD_CIRCLE_DIAMETER,
)
from material_drawing_generator.materials import MaterialLibrary
from material_drawing_generator.models import SectionRow, SheetData, WorkbookData, group_section_rows
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.drawing import DrawingMetadata, _converter_root, convert_dxf_to_dwg, render_dxf
from material_drawing_generator.material_drawing import prepare_material_job, verify_material_cells, validate_material_geometry
from material_drawing_generator.text_pdf import TextPdfBook
from unittest.mock import patch


class MaterialSymbolImportTests(unittest.TestCase):
    def make_source(self, path):
        font = _cjk_font_filename()
        doc = ezdxf.new("R2010")
        doc.styles.new("PARTLIST", dxfattribs={"font": font})
        msp = doc.modelspace()
        msp.add_circle((10000, -5000), 500)
        msp.add_line((9500, -5000), (10500, -5000))
        msp.add_line((10000, -5500), (10000, -4500))
        msp.add_text("A", dxfattribs={"insert": (10600, -5050), "height": 100, "style": "PARTLIST"})
        doc.saveas(path)

    def test_circle_normalization_preview_portable_library_and_cad_block(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.dxf"
            custom = Path(folder) / "custom.json"
            self.make_source(source)
            source_bytes = source.read_bytes()
            raw, circles = inspect_drawing(source, _cjk_font_filename())
            self.assertEqual(len(circles), 1)
            rule = build_rule("TEST-GRADE", ("TG",), raw, "circle", 0, _cjk_font_filename())
            circle = next(item for item in rule.geometry if item[0] == "CIRCLE")
            self.assertAlmostEqual(circle[3] * 2, STANDARD_CIRCLE_DIAMETER, places=7)
            text = next(item for item in rule.geometry if item[0] == "TEXT")
            self.assertEqual(text[1], "A")
            self.assertGreater(text[2], rule.geometry_bounds[2] * 0.5)
            self.assertEqual(source.read_bytes(), source_bytes)
            png = symbol_preview_png(rule, _cjk_font_filename(), 120, 240)
            self.assertTrue(png.startswith(b"\x89PNG"))
            append_custom_rule(rule, custom)
            source.unlink()
            loaded = MaterialLibrary.load(custom_path=custom).resolve("tg")
            self.assertEqual(loaded.geometry, rule.geometry)
            doc = ezdxf.new("R2007")
            factory = SymbolFactory(_cjk_font_filename())
            block_name = factory.ensure_block(doc, loaded, "Standard")
            self.assertEqual(len(doc.blocks.get(block_name)), len(loaded.geometry))
            doc.modelspace().add_blockref(block_name, (0, 0))
            self.assertEqual(len(material_geometry_signature(doc)[1]), 1)

    def test_height_mode_and_duplicate_or_unsupported_files_leave_library_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.dxf"
            custom = Path(folder) / "custom.json"
            self.make_source(source)
            raw, _ = inspect_drawing(source, _cjk_font_filename())
            rule = build_rule("HEIGHT-GRADE", (), raw, "height", font_filename=_cjk_font_filename())
            self.assertAlmostEqual(rule.geometry_bounds[3] - rule.geometry_bounds[1], STANDARD_CIRCLE_DIAMETER, places=6)
            append_custom_rule(rule, custom)
            before = custom.read_bytes()
            with self.assertRaisesRegex(ValueError, "重复"):
                append_custom_rule(rule, custom)
            self.assertEqual(custom.read_bytes(), before)
            doc = ezdxf.new("R2010")
            doc.modelspace().add_point((0, 0))
            doc.saveas(source)
            with self.assertRaisesRegex(ValueError, "不支持图元"):
                inspect_drawing(source, _cjk_font_filename())
            self.assertEqual(custom.read_bytes(), before)

    def test_hidden_or_noncontinuous_entities_are_rejected(self):
        cases = ("off", "frozen", "nonplot", "layer_linetype", "entity_linetype", "byblock")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "invalid.dxf"
                doc = ezdxf.new("R2010")
                msp = doc.modelspace()
                layer = "0"
                if case == "off":
                    layer = "OFF"
                    doc.layers.new(layer, dxfattribs={"color": -7})
                elif case == "frozen":
                    layer = "FROZEN"
                    doc.layers.new(layer, dxfattribs={"flags": 1})
                elif case == "nonplot":
                    layer = "NOPLOT"
                    doc.layers.new(layer, dxfattribs={"plot": 0})
                elif case == "layer_linetype":
                    layer = "DASHED_LAYER"
                    doc.linetypes.new("DASHED_QA", dxfattribs={"pattern": [0.5, 0.25, -0.25]})
                    doc.layers.new(layer, dxfattribs={"linetype": "DASHED_QA"})
                line = msp.add_line((0, 0), (1, 1), dxfattribs={"layer": layer})
                if case == "entity_linetype":
                    line.dxf.linetype = "DASHED_QA"
                elif case == "byblock":
                    line.dxf.linetype = "BYBLOCK"
                doc.saveas(path)
                with self.assertRaisesRegex(ValueError, "关闭图层|冻结图层|不可打印图层|非连续线型|BYBLOCK"):
                    inspect_drawing(path, _cjk_font_filename())

    def test_style_level_text_distortions_are_rejected(self):
        for attributes in ({"width": 2}, {"oblique": 25}, {"flags": 1}, {"height": 2}):
            with self.subTest(attributes=attributes), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "text-style.dxf"
                doc = ezdxf.new("R2010")
                doc.styles.new("PARTLIST", dxfattribs={"font": _cjk_font_filename(), **attributes})
                doc.modelspace().add_text("A", dxfattribs={"insert": (0, 0), "height": 1, "style": "PARTLIST"})
                doc.saveas(path)
                with self.assertRaisesRegex(ValueError, "文字样式.*非默认"):
                    inspect_drawing(path, _cjk_font_filename())

    def test_symbol_factory_measures_actual_bounds_instead_of_trusting_json(self):
        rule = build_rule("BOUND-QA", (), [("CIRCLE", 0, 0, 20)], "height")
        payload_rule = rule.to_record()
        payload_rule["geometry_bounds"] = [0, -0.1, 0.2, 0.1]
        with tempfile.TemporaryDirectory() as folder:
            custom = Path(folder) / "custom.json"
            custom.write_text(json.dumps({"schema_version": 1, "symbols": [payload_rule]}), encoding="utf-8")
            loaded = MaterialLibrary.load(custom_path=custom).resolve("BOUND-QA")
            with self.assertRaisesRegex(ValueError, "边界与实际图元"):
                SymbolFactory(_cjk_font_filename()).geometry(loaded)

    def test_custom_schema_keeps_old_records_and_rejects_unknown_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            custom = Path(folder) / "custom.json"
            custom.write_text(json.dumps({"schema_version": 1, "symbols": [{
                "material": "OLD", "aliases": [], "prefix": "T", "inner": "", "shape": "circle",
            }]}), encoding="utf-8")
            rule = build_rule("NEW", (), [("CIRCLE", 0, 0, 10)], "height")
            append_custom_rule(rule, custom)
            loaded = MaterialLibrary.load(custom_path=custom)
            self.assertEqual(loaded.resolve("OLD").shape, "circle")
            self.assertEqual(loaded.resolve("NEW").geometry[0][0], "CIRCLE")
            before = custom.read_bytes()
            custom.write_text('{"schema_version":9,"symbols":[]}', encoding="utf-8")
            unknown = custom.read_bytes()
            with self.assertRaises(ValueError):
                append_custom_rule(rule, custom)
            self.assertEqual(custom.read_bytes(), unknown)
            self.assertNotEqual(before, unknown)

    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").is_file(), "LibreDWG writer unavailable")
    def test_imported_arc_symbol_exports_pdf_and_roundtrips_through_dwg(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "arc.dxf"
            custom = root / "custom.json"
            doc = ezdxf.new("R2010")
            doc.styles.new("PARTLIST", dxfattribs={"font": _cjk_font_filename()})
            msp = doc.modelspace()
            msp.add_circle((10000, 10000), 1000)
            msp.add_line((9000, 10000), (11000, 10000))
            msp.add_arc((10000, 10000), 600, 15, 300)
            msp.add_text("A", dxfattribs={"insert": (11200, 9950), "height": 150, "style": "PARTLIST"})
            doc.saveas(source)
            no_text = root / "dwg_input.dxf"
            dwg_source = ezdxf.new("R2010")
            dwg_source.modelspace().add_circle((0, 0), 1000)
            dwg_source.modelspace().add_arc((0, 0), 600, 15, 300)
            dwg_source.modelspace().add_line((-1000, 0), (1000, 0))
            dwg_source.saveas(no_text)
            source_dwg = root / "source.dwg"
            convert_dxf_to_dwg(no_text, source_dwg)
            source_dwg_bytes = source_dwg.read_bytes()
            imported_dwg_raw, _ = inspect_drawing(source_dwg, _cjk_font_filename())
            self.assertEqual(source_dwg.read_bytes(), source_dwg_bytes)
            self.assertTrue(any(item[0] == "ARC" for item in imported_dwg_raw))
            raw, _ = inspect_drawing(source, _cjk_font_filename())
            rule = build_rule("IMPORT-QA", (), raw, "circle", 0, _cjk_font_filename())
            append_custom_rule(rule, custom)
            loaded_library = MaterialLibrary.load(custom_path=custom)
            row = SectionRow(2, "G1", "大梁", section="H-100X50", material="IMPORT-QA")
            sheet = SheetData("IMPORT-QA", 1, [row], group_section_rows([row], ["大梁"]))
            workbook = WorkbookData("temporary.xlsx", [sheet])
            with patch("material_drawing_generator.material_drawing.MaterialLibrary.load", return_value=loaded_library):
                _, plans = prepare_material_job(workbook, _cjk_font_filename())
            plan = plans[sheet.name]
            exported = root / "imported.dxf"
            template = SiteTemplate.load(default_template_path())
            template.data["material_symbols"] = {"enabled": True}
            render_dxf(exported, sheet, template, DrawingMetadata(), plan)
            before = ezdxf.readfile(exported)
            verify_material_cells(before, plan)
            pdf = TextPdfBook(root / "imported.pdf", 420, 297)
            pdf.add_cad_page(before, "IMPORT-QA", expected_sections=["H-100X50"])
            pdf.save()
            self.assertGreater((root / "imported.pdf").stat().st_size, 1000)
            dwg = root / "imported.dwg"
            convert_dxf_to_dwg(exported, dwg)
            roundtrip = root / "roundtrip.dxf"
            tool = _converter_root() / "dwg2dxf.exe"
            result = subprocess.run([str(tool), "-y", "-o", str(roundtrip), str(dwg)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(roundtrip.exists())
            after = ezdxf.readfile(roundtrip)
            validate_material_geometry(before, after)


if __name__ == "__main__":
    unittest.main()
