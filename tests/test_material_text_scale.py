"""Regression coverage for imported-symbol TEXT-height normalization."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ezdxf

from material_drawing_generator.drawing import (
    DrawingMetadata, _cjk_font_filename, _converter_root, convert_dxf_to_dwg, render_dxf,
)
from material_drawing_generator.material_drawing import (
    SymbolFactory, prepare_material_job, validate_material_geometry,
)
from material_drawing_generator.material_preview import symbol_section_preview_png
from material_drawing_generator.material_symbol_import import (
    append_custom_rule, build_rule, inspect_drawing, text_reference_index,
)
from material_drawing_generator.materials import MaterialLibrary
from material_drawing_generator.models import (
    SectionRow, SheetData, WorkbookData, group_section_rows,
)
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.text_pdf import TextPdfBook


def polygon_text_raw(sides, height, label="N", radius=24.0, angle=0.0):
    """Return a large outline polygon and one TEXT entity, without circles."""
    import math

    points = [
        (radius * math.cos(angle + 2 * math.pi * i / sides), radius * math.sin(angle + 2 * math.pi * i / sides))
        for i in range(sides)
    ]
    raw = []
    for a, b in zip(points, points[1:] + points[:1]):
        raw.append(("LINE", a[0], a[1], b[0], b[1]))
    raw.append(("TEXT", label, 0.0, 0.0, float(height), 0.0, 1.0))
    return raw


class MaterialTextScaleTests(unittest.TestCase):
    def test_text_mode_normalizes_large_circle_free_outlines_and_preserves_shape_ratio(self):
        font = _cjk_font_filename()
        for name, sides, label, source_height, angle in (
            ("square", 4, "N", 8, 0.7853981633974483), ("diamond", 4, "T", 13, 0.0),
            ("pentagon", 5, "B", 21, 0.0), ("hexagon", 6, "N", 34, 0.0),
        ):
            with self.subTest(shape=name):
                raw = polygon_text_raw(sides, source_height, label, angle=angle)
                rule = build_rule(name, (), raw, "text", font_filename=font)
                text = next(item for item in rule.geometry if item[0] == "TEXT")
                self.assertEqual(text[1], label)
                self.assertAlmostEqual(text[4], 1.0)
                source_lines = [item for item in raw if item[0] == "LINE"]
                output_lines = [item for item in rule.geometry if item[0] == "LINE"]
                self.assertEqual(len(output_lines), sides)
                scale = 1.0 / source_height
                source_lengths = [((v[3] - v[1]) ** 2 + (v[4] - v[2]) ** 2) ** 0.5 for v in source_lines]
                output_lengths = [((v[3] - v[1]) ** 2 + (v[4] - v[2]) ** 2) ** 0.5 for v in output_lines]
                for before, after in zip(source_lengths, output_lengths):
                    self.assertAlmostEqual(after, before * scale, places=7)

    def test_reference_selection_same_size_mixed_size_missing_text_and_bad_index(self):
        same = polygon_text_raw(4, 12, "N") + [("TEXT", "T", 3, 2, 12.0, 0.0, 1.0)]
        self.assertEqual(text_reference_index(same), 0)
        mixed = polygon_text_raw(5, 12, "N") + [("TEXT", "T", 3, 2, 6.0, 0.0, 1.0)]
        with self.assertRaisesRegex(ValueError, "不同字号"):
            text_reference_index(mixed)
        self.assertEqual(text_reference_index(mixed, 1), 1)
        rule = build_rule("MIXED", (), mixed, "text", font_filename=_cjk_font_filename(), text_index=1)
        self.assertEqual(rule.baseline_text_index, 1)
        self.assertEqual(rule.to_record()["baseline_text_index"], 1)
        heights = [item[4] for item in rule.geometry if item[0] == "TEXT"]
        self.assertEqual(heights, [2.0, 1.0])
        with self.assertRaisesRegex(ValueError, "没有普通 TEXT"):
            text_reference_index([("LINE", 0, 0, 1, 1)])
        with self.assertRaisesRegex(ValueError, "没有普通 TEXT"):
            build_rule("NO-TEXT", (), [("LINE", 0, 0, 1, 1)], "text")
        with self.assertRaisesRegex(ValueError, "有效的基准文字"):
            text_reference_index(same, 2)
        with self.assertRaisesRegex(ValueError, "基线文字索引无效"):
            type(rule)(rule.material, geometry=rule.geometry, geometry_bounds=rule.geometry_bounds,
                       baseline_text_index=True)
        rotated = polygon_text_raw(4, 10, "N")
        rotated[-1] = ("TEXT", "N", 0.0, 0.0, 10.0, 90.0, 1.0)
        with self.assertRaisesRegex(ValueError, "水平旋转"):
            build_rule("ROTATED", (), rotated, "text", font_filename=_cjk_font_filename())

    def test_legacy_imported_geometry_without_anchor_roundtrips_unchanged(self):
        import json

        with tempfile.TemporaryDirectory() as folder:
            custom = Path(folder) / "custom.json"
            raw = polygon_text_raw(4, 10, "N")
            rule = build_rule("NEW-ANCHOR", (), raw, "text", font_filename=_cjk_font_filename())
            record = rule.to_record()
            self.assertEqual(record["baseline_text_index"], 0)
            legacy = dict(record)
            legacy.pop("baseline_text_index")
            legacy["material"] = "LEGACY"
            custom.write_text(json.dumps({"schema_version": 1, "symbols": [legacy]}), encoding="utf-8")
            loaded = MaterialLibrary.load(custom_path=custom).resolve("LEGACY")
            self.assertIsNone(loaded.baseline_text_index)
            self.assertNotIn("baseline_text_index", loaded.to_record())
            self.assertEqual(SymbolFactory(_cjk_font_filename()).geometry(loaded).primitives, loaded.geometry)

    def test_non_text_modes_remain_compatible(self):
        raw = [("LINE", -3, -2, 3, -2), ("LINE", 3, -2, 3, 2),
               ("LINE", 3, 2, -3, 2), ("LINE", -3, 2, -3, -2),
               ("CIRCLE", 0.0, 0.0, 2.0)]
        height_rule = build_rule("HEIGHT", (), raw, "height")
        self.assertAlmostEqual(height_rule.geometry_bounds[3] - height_rule.geometry_bounds[1], 1.7)
        circle_rule = build_rule("CIRCLE", (), raw, "circle", circle_index=0)
        circle = next(item for item in circle_rule.geometry if item[0] == "CIRCLE")
        self.assertAlmostEqual(circle[3] * 2, 1.7)

    def test_gui_text_preview_cancel_save_and_reload_use_only_isolated_library(self):
        import json
        import os
        import shutil
        import tkinter as tk
        from material_drawing_generator import materials, material_symbol_import
        from material_drawing_generator.gui import MaterialDrawingApp

        original_root = materials.resource_root()
        original_custom = original_root / "material_libraries" / "custom.json"
        original_bytes = original_custom.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            sandbox = Path(folder)
            library_dir = sandbox / "material_libraries"
            library_dir.mkdir()
            shutil.copy2(original_root / "material_libraries" / "base.json", library_dir / "base.json")
            custom = library_dir / "custom.json"
            custom.write_text('{"schema_version":1,"symbols":[]}', encoding="utf-8")
            source = sandbox / "polygon.dxf"
            doc = ezdxf.new("R2007")
            doc.styles.new("QA", dxfattribs={"font": _cjk_font_filename()})
            doc.modelspace().add_lwpolyline([(0, 0), (160, 0), (160, 160), (0, 160)], close=True)
            doc.modelspace().add_text("N", dxfattribs={"style": "QA", "height": 100, "insert": (50, 30)})
            doc.saveas(source)
            env = {key: str(sandbox) for key in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA")}
            # Patch both path providers before the dialog captures its imports.
            # Real append/load calls are exercised, never the project's library.
            with patch.dict(os.environ, env), \
                    patch.object(materials, "resource_root", return_value=sandbox), \
                    patch.object(material_symbol_import, "resource_root", return_value=sandbox):
                root = tk.Tk()
                root.withdraw()
                try:
                    app = MaterialDrawingApp(root)
                    library_window = tk.Toplevel(root)
                    library_window.withdraw()
                    with patch("material_drawing_generator.gui.messagebox.showerror") as error, \
                            patch("material_drawing_generator.gui.messagebox.askyesno", side_effect=[False, True]) as confirm, \
                            patch.object(app, "_show_material_library") as refresh:
                        app._import_material_symbol(library_window)
                        dialog = next(widget for widget in library_window.winfo_children() if isinstance(widget, tk.Toplevel))
                        dialog.withdraw()
                        form = dialog.winfo_children()[0]
                        name = form.grid_slaves(row=0, column=1)[0]
                        name.insert(0, "UI-TEXT-TEST")
                        path = form.grid_slaves(row=2, column=1)[0].grid_slaves(row=0, column=0)[0]
                        path.insert(0, str(source))
                        modes = form.grid_slaves(row=3, column=1)[0].winfo_children()
                        self.assertEqual(root.getvar(str(modes[0].cget("variable"))), "text")
                        preview = form.grid_slaves(row=6, column=1)[0]
                        save = form.grid_slaves(row=9, column=1)[0]
                        preview.invoke()
                        error.assert_not_called()
                        self.assertTrue(form.grid_slaves(row=5, column=1)[0].get())
                        name.insert("end", "-CHANGED")
                        save.invoke()
                        self.assertEqual(error.call_count, 1)
                        confirm.assert_not_called()
                        self.assertEqual(json.loads(custom.read_text())["symbols"], [])
                        error.reset_mock()
                        preview.invoke()
                        save.invoke()  # User cancels confirmation.
                        self.assertEqual(json.loads(custom.read_text())["symbols"], [])
                        save.invoke()  # User confirms, using the actual append function.
                        error.assert_not_called()
                        refresh.assert_called_once()
                        source.unlink()
                        rule = MaterialLibrary.load().resolve("UI-TEXT-TEST-CHANGED")
                        self.assertAlmostEqual(next(item[4] for item in rule.geometry if item[0] == "TEXT"), 1)
                        self.assertEqual(len(json.loads(custom.read_text())["symbols"]), 1)
                finally:
                    root.destroy()
                    self.assertEqual(original_custom.read_bytes(), original_bytes)

    def test_preview_uses_one_scene_and_one_world_scale_for_symbol_and_section(self):
        from material_drawing_generator import material_preview

        rule = build_rule("PREVIEW", (), polygon_text_raw(5, 16, "N"), "text",
                          font_filename=_cjk_font_filename())
        captured = {}

        def capture(scene, height_px, max_width_px):
            captured["entities"] = list(scene)
            captured["limits"] = (height_px, max_width_px)
            return b"preview"

        with patch.object(material_preview, "_render_block_png", side_effect=capture):
            result = symbol_section_preview_png(rule, _cjk_font_filename(), 120, 520, "H-300X150")
        self.assertEqual(result, b"preview")
        texts = [entity for entity in captured["entities"] if entity.dxftype() == "TEXT"]
        self.assertEqual({entity.dxf.text for entity in texts}, {"N", "H-300X150"})
        self.assertTrue(all(abs(entity.dxf.height - 1.0) < 1e-9 for entity in texts))
        self.assertAlmostEqual(texts[0].dxf.insert.y, texts[1].dxf.insert.y, places=7)
        self.assertEqual(captured["limits"], (120, 520))

    def test_japanese_gui_no_text_error_is_translated_and_cannot_save(self):
        import os
        import tkinter as tk
        from material_drawing_generator.gui import MaterialDrawingApp
        from material_drawing_generator import materials

        original_custom = materials.resource_root() / "material_libraries" / "custom.json"
        original_bytes = original_custom.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "outline-only.dxf"
            document = ezdxf.new("R2007")
            document.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
            document.saveas(source)
            with patch.dict(os.environ, {key: folder for key in ("TEMP", "TMP", "LOCALAPPDATA", "APPDATA")}):
                root = tk.Tk()
                root.withdraw()
                try:
                    app = MaterialDrawingApp(root, language="ja")
                    library_window = tk.Toplevel(root)
                    library_window.withdraw()
                    with patch("material_drawing_generator.gui.messagebox.showerror") as error, \
                            patch("material_drawing_generator.gui.messagebox.askyesno", return_value=False) as confirm:
                        app._import_material_symbol(library_window)
                        dialog = next(widget for widget in library_window.winfo_children() if isinstance(widget, tk.Toplevel))
                        dialog.withdraw()
                        form = dialog.winfo_children()[0]
                        form.grid_slaves(row=0, column=1)[0].insert(0, "OUTLINE-ONLY")
                        form.grid_slaves(row=2, column=1)[0].grid_slaves(row=0, column=0)[0].insert(0, str(source))
                        form.grid_slaves(row=6, column=1)[0].invoke()
                        self.assertEqual(error.call_count, 1)
                        self.assertEqual(error.call_args.args[1], app.tr("material_import_no_text"))
                        self.assertNotEqual(app.tr("material_import_no_text"), "material_import_no_text")
                        form.grid_slaves(row=9, column=1)[0].invoke()
                        self.assertEqual(error.call_args.args[1], app.tr("material_import_preview_first"))
                        confirm.assert_not_called()
                finally:
                    root.destroy()
                    self.assertEqual(original_custom.read_bytes(), original_bytes)

    def _make_export(self, root):
        font = _cjk_font_filename()
        source_path = root / "source.dxf"
        source_doc = ezdxf.new("R2010")
        source_doc.styles.new("PARTLIST", dxfattribs={"font": font})
        source_msp = source_doc.modelspace()
        for entity in polygon_text_raw(6, 18, "B", radius=12):
            if entity[0] == "LINE":
                source_msp.add_line(entity[1:3], entity[3:5])
            elif entity[0] == "TEXT":
                source_msp.add_text(entity[1], dxfattribs={
                    "insert": (entity[2], entity[3]), "height": entity[4], "style": "PARTLIST",
                })
        source_doc.saveas(source_path)
        raw, circles = inspect_drawing(source_path, font)
        self.assertFalse(circles)
        rule = build_rule("TEXT-GRADE", ("TG",), raw, "text", font_filename=font)
        custom = root / "custom.json"
        append_custom_rule(rule, custom)
        loaded = MaterialLibrary.load(custom_path=custom)
        row = SectionRow(2, "G1", "大梁", section="H-300X150X6.5X9", material="TG")
        sheet = SheetData("TEXT-GRADE", 1, [row], group_section_rows([row], ["大梁"]))
        workbook = WorkbookData("temporary.xlsx", [sheet])
        with patch("material_drawing_generator.material_drawing.MaterialLibrary.load", return_value=loaded):
            _library, plans = prepare_material_job(workbook, font)
        template = SiteTemplate.load(default_template_path())
        template.data["material_symbols"] = {"enabled": True}
        dxf_path = root / "text-symbol.dxf"
        render_dxf(dxf_path, sheet, template, DrawingMetadata(), plans[sheet.name])
        return rule, custom, dxf_path, source_path

    def test_persisted_symbol_exports_text_at_section_height_and_pdf_text(self):
        from material_drawing_generator.text_pdf import TextBackend

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rule, custom, dxf_path, source = self._make_export(root)
            # The persisted rule is independent of the original CAD source.
            custom_bytes = custom.read_bytes()
            source.unlink()
            loaded = MaterialLibrary.load(custom_path=custom).resolve("TG")
            self.assertEqual(loaded.geometry, rule.geometry)
            self.assertEqual(custom.read_bytes(), custom_bytes)

            drawing = ezdxf.readfile(dxf_path)
            modelspace = drawing.modelspace()
            section = next(entity for entity in modelspace.query("TEXT") if entity.dxf.text == "H-300X150X6.5X9")
            insert = next(entity for entity in modelspace.query("INSERT") if entity.dxf.name == rule.block_name)
            block_texts = [entity for entity in drawing.blocks.get(insert.dxf.name).query("TEXT")]
            symbol = next(entity for entity in block_texts if entity.dxf.text == "B")
            self.assertAlmostEqual(symbol.dxf.height * insert.dxf.yscale, section.dxf.height, places=7)
            self.assertAlmostEqual(
                symbol.dxf.insert.y * insert.dxf.yscale + insert.dxf.insert.y,
                section.dxf.insert.y,
                places=7,
            )
            self.assertAlmostEqual(symbol.dxf.height, 1.0, places=7)

            pdf_path = root / "text-symbol.pdf"
            pdf = TextPdfBook(pdf_path, 420, 297)
            emitted = []
            original_native_text = TextBackend.native_text

            def record_pdf_text(backend, text, *args, **kwargs):
                emitted.append(text)
                return original_native_text(backend, text, *args, **kwargs)

            with patch.object(TextBackend, "native_text", new=record_pdf_text):
                pdf.add_cad_page(drawing, "TEXT-GRADE", expected_sections=["H-300X150X6.5X9"])
                pdf.save()
            self.assertIn("H-300X150X6.5X9", emitted)
            self.assertIn("B", emitted)
            self.assertGreater(pdf_path.stat().st_size, 1000)

    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").is_file(), "LibreDWG writer unavailable")
    def test_dwg_roundtrip_retains_text_symbol_and_section(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rule, _custom, dxf_path, _source = self._make_export(root)
            dwg_path = root / "text-symbol.dwg"
            convert_dxf_to_dwg(dxf_path, dwg_path)
            roundtrip = root / "roundtrip.dxf"
            result = subprocess.run(
                [str(_converter_root() / "dwg2dxf.exe"), "-y", "-o", str(roundtrip), str(dwg_path)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(roundtrip.is_file())
            before, after = ezdxf.readfile(dxf_path), ezdxf.readfile(roundtrip)
            validate_material_geometry(before, after)
            texts = [entity.dxf.text for entity in after.modelspace().query("TEXT")]
            self.assertIn("H-300X150X6.5X9", texts)
            self.assertTrue(any(entity.dxf.name == rule.block_name for entity in after.modelspace().query("INSERT")))


if __name__ == "__main__":
    unittest.main()
