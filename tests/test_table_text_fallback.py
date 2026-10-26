from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import ezdxf
from ezdxf.fonts import fonts

from material_drawing_generator.drawing import (
    DrawingError, _cjk_font_filename, _prepare_tables,
)
from material_drawing_generator.models import SectionRow, SheetData, WorkbookData, group_section_rows
from material_drawing_generator.service import generate_drawings
from material_drawing_generator.excel_marker import HighlightResult
from material_drawing_generator.settings import UserSettings
from material_drawing_generator.site_template_store import SiteTemplateStore
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.table_layout_policy import validate_auto_text_shrink_steps
from material_drawing_generator.i18n import translator

ROOT = Path(__file__).resolve().parents[1]


def make_sheet(rows):
    template = SiteTemplate.load(default_template_path())
    return SheetData("2F", 1, rows, group_section_rows(rows, template.category_order))


def required_width(sheet, factor, material_plan=None):
    template = SiteTemplate.load(default_template_path())
    font = fonts.make_font(_cjk_font_filename(), 1.0)
    groups = sheet.groups_for_category(sheet.categories[0])
    table = template.table_for_category(sheet.categories[0])
    total = 0.0
    for col in table["columns"]:
        if col["field"] == "material":
            continue
        values = [col["label"]]
        values.extend(row.value_for(col["field"]) for group in groups for row in group.rows)
        measured = [font.text_width_ex(value, factor) for value in values]
        if material_plan is not None and col["field"] == "section":
            measured.extend(material_plan.column_width(row, factor) for group in groups for row in group.rows)
        total += max(measured) + 1.21
    return total


class TableTextFallbackTests(unittest.TestCase):
    def setUp(self):
        self.template = SiteTemplate.load(default_template_path())

    def test_strategy_is_strict_integer_zero_one_or_two(self):
        for step in (0, 1, 2):
            self.assertEqual(validate_auto_text_shrink_steps(step), step)
        for bad in (True, False, 0.0, 1.0, "1", "2", -1, 3, math.nan):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                validate_auto_text_shrink_steps(bad)

    def test_zero_keeps_original_size_and_existing_continuation_does_not_shrink(self):
        rows = [SectionRow(i + 2, "BeamA", "大梁", position=f"P{i}", section=f"H-{i}") for i in range(100)]
        sheet = make_sheet(rows)
        tables = _prepare_tables(sheet, self.template)
        self.assertGreater(len(tables), 1)
        self.assertEqual({size for _, _, size in tables}, {1.0})
        self.assertEqual({table["row_height"] for table, _, _ in tables}, {2.5})
        self.assertEqual({table["text_shrink_steps"] for table, _, _ in tables}, {0})

    def test_width_pressure_selects_first_fitting_step_and_preserves_original_template(self):
        sheet = make_sheet([SectionRow(2, "梁" * 35, "大梁", position="中央", section="H-100")])
        base = required_width(sheet, 1.0)
        one_step = required_width(sheet, 0.9)
        self.assertGreater(base, one_step)
        layout = self.template.data["dynamic_table_layout"]
        left = float(layout.get("left", 16.0))
        layout["right"] = left + (base + one_step) / 2
        layout["auto_text_shrink_steps"] = 1
        tables = _prepare_tables(sheet, self.template)
        self.assertEqual({table["text_shrink_steps"] for table, _, _ in tables}, {1})
        self.assertAlmostEqual(tables[0][2], 0.9)
        self.assertAlmostEqual(tables[0][0]["row_height"], 2.25)
        self.assertEqual(self.template.data["dynamic_table_layout"]["fixed_text_height"], 1.0)
        self.assertEqual(self.template.data["dynamic_table_layout"]["fixed_row_height"], 2.5)

    def test_two_step_case_and_one_step_limit_still_fails(self):
        sheet = make_sheet([SectionRow(2, "梁" * 38, "大梁", position="中央", section="H-100")])
        one_step = required_width(sheet, 0.9)
        two_step = required_width(sheet, 0.8)
        self.assertGreater(one_step, two_step)
        left = float(self.template.data["dynamic_table_layout"].get("left", 16.0))
        one_narrow = SiteTemplate.load(default_template_path())
        one_narrow.data["dynamic_table_layout"].update({"right": left + (one_step + two_step) / 2, "auto_text_shrink_steps": 1})
        with self.assertRaises(DrawingError):
            _prepare_tables(sheet, one_narrow)
        two_allowed = SiteTemplate.load(default_template_path())
        two_allowed.data["dynamic_table_layout"].update({"right": left + (one_step + two_step) / 2, "auto_text_shrink_steps": 2})
        tables = _prepare_tables(sheet, two_allowed)
        self.assertEqual({table["text_shrink_steps"] for table, _, _ in tables}, {2})
        self.assertEqual({table["actual_text_height"] for table, _, _ in tables}, {0.8})
        self.assertEqual({table["actual_row_height"] for table, _, _ in tables}, {2.0})

    def test_all_categories_share_one_fallback_step_and_keep_source_rows(self):
        rows = [
            SectionRow(2, "Beam" * 9, "大梁", position="中央", section="H-100"),
            SectionRow(3, "小梁1", "小梁", position="端部", section="H-200"),
        ]
        sheet = make_sheet(rows)
        template = SiteTemplate.load(default_template_path())
        needs0 = sum(required_width(SheetData(sheet.name, sheet.header_row, [r], group_section_rows([r], template.category_order)), 1.0) for r in rows)
        needs1 = sum(required_width(SheetData(sheet.name, sheet.header_row, [r], group_section_rows([r], template.category_order)), 0.9) for r in rows)
        left = float(template.data["dynamic_table_layout"].get("left", 16.0))
        gap = float(template.data["dynamic_table_layout"].get("horizontal_gap", 8.0))
        template.data["dynamic_table_layout"].update({"right": left + (needs0 + needs1) / 2 + gap, "auto_text_shrink_steps": 1})
        tables = _prepare_tables(sheet, template)
        self.assertEqual({table["text_shrink_steps"] for table, _, _ in tables}, {1})
        self.assertEqual([row.source_row for _, groups, _ in tables for group in groups for row in group.rows], [2, 3])

    def test_unknown_or_invalid_layout_errors_are_not_retried_as_shrinkable(self):
        sheet = make_sheet([SectionRow(2, "G1", "大梁", section="H-100")])
        bad_policy = SiteTemplate.load(default_template_path())
        bad_policy.data["dynamic_table_layout"]["auto_text_shrink_steps"] = True
        with self.assertRaises(ValueError):
            _prepare_tables(sheet, bad_policy)
        legacy_small = SiteTemplate.load(default_template_path())
        legacy_small.data["dynamic_table_layout"].update({"fixed_text_height": 0.5, "fixed_row_height": 1.8})
        self.assertEqual(_prepare_tables(sheet, legacy_small)[0][2], 0.5)
        too_small_to_fallback = SiteTemplate.load(default_template_path())
        too_small_to_fallback.data["dynamic_table_layout"].update({
            "fixed_text_height": 0.7, "fixed_row_height": 2.1, "auto_text_shrink_steps": 1,
        })
        long_sheet = make_sheet([SectionRow(2, "梁" * 35, "大梁", section="H-100")])
        left = float(too_small_to_fallback.data["dynamic_table_layout"].get("left", 16.0))
        too_small_to_fallback.data["dynamic_table_layout"]["right"] = left + required_width(long_sheet, 0.7) - 0.02
        with self.assertRaisesRegex(DrawingError, "安全下限"):
            _prepare_tables(long_sheet, too_small_to_fallback)

    def test_service_invalid_policy_fails_before_read_or_marker(self):
        template = SiteTemplate.load(default_template_path())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch("material_drawing_generator.service.read_workbook", side_effect=AssertionError("read Excel")) as reader, patch(
                "material_drawing_generator.service.apply_submission_highlighting", side_effect=AssertionError("mark Excel")
            ):
                for value in (True, 1.0, "2", -1, 3):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        generate_drawings("missing.xlsx", output, template, {"dxf"}, auto_text_shrink_steps=value)
                reader.assert_not_called()
            self.assertFalse(output.exists())

        invalid_template = SiteTemplate.load(default_template_path())
        invalid_template.data["dynamic_table_layout"]["auto_text_shrink_steps"] = 1.0
        with patch("material_drawing_generator.service.read_workbook", side_effect=AssertionError("read Excel")) as reader:
            with self.assertRaises(ValueError):
                generate_drawings("missing.xlsx", Path(tempfile.gettempdir()) / "must-not-create", invalid_template, {"dxf"})
            reader.assert_not_called()

    def test_two_step_fit_failure_stops_before_excel_marking_and_output_creation(self):
        sheet = make_sheet([SectionRow(2, "梁" * 1000, "大梁", section="H-100")])
        template = SiteTemplate.load(default_template_path())
        template.data["dynamic_table_layout"]["auto_text_shrink_steps"] = 2
        with tempfile.TemporaryDirectory(dir=ROOT / ".test-temp" / "shrink-dev") as directory:
            output = Path(directory) / "must-not-create"
            with patch("material_drawing_generator.service.read_workbook", return_value=WorkbookData("synthetic", [sheet])), patch(
                "material_drawing_generator.service.apply_submission_highlighting",
                side_effect=AssertionError("must not mark Excel when all steps fail"),
            ):
                with self.assertRaises(DrawingError):
                    generate_drawings("synthetic.xlsx", output, template, {"dxf"})
            self.assertFalse(output.exists())

    def test_cli_strategy_is_optional_and_limited_to_three_choices(self):
        from main import build_parser
        parser = build_parser("zh")
        self.assertIsNone(parser.parse_args(["--input", "x.xlsx"]).auto_text_shrink_steps)
        self.assertEqual(parser.parse_args(["--input", "x.xlsx", "--auto-text-shrink-steps", "2"]).auto_text_shrink_steps, 2)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--input", "x.xlsx", "--auto-text-shrink-steps", "3"])

    def test_service_reports_selected_step_and_warning_and_scales_same_layout(self):
        rows = [SectionRow(2, "梁" * 35, "大梁", position="中央", section="H-100")]
        sheet = make_sheet(rows)
        template = SiteTemplate.load(default_template_path())
        base, reduced = required_width(sheet, 1.0), required_width(sheet, 0.9)
        left = float(template.data["dynamic_table_layout"].get("left", 16.0))
        template.data["dynamic_table_layout"].update({"right": left + (base + reduced) / 2, "auto_text_shrink_steps": 1})
        workbook = WorkbookData("synthetic.xlsx", [sheet])
        with tempfile.TemporaryDirectory() as directory, patch(
            "material_drawing_generator.service.read_workbook", return_value=workbook
        ), patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)):
            result = generate_drawings("synthetic.xlsx", Path(directory) / "out", template, {"pdf", "dxf"}, cad_output_scale=150)
            generated = result.sheets[0]
            self.assertEqual(generated.table_layout["actual_steps"], 1)
            self.assertTrue(generated.warnings)
            report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
            self.assertEqual(report["table_layout_policy"]["sheets"]["2F"]["actual_steps"], 1)
            self.assertEqual(report["table_layout_policy"]["sheets"]["2F"]["actual_text_height"], 0.9)
            doc = ezdxf.readfile(generated.files["dxf"])
            text = next(entity for entity in doc.modelspace().query("TEXT") if entity.dxf.text.startswith("H-100"))
            self.assertAlmostEqual(text.dxf.height, 0.9 * 150)
            self.assertTrue(Path(generated.files["pdf"]).exists())

    def test_material_text_baseline_and_height_survive_default_cad_multiplier_dwg_roundtrip(self):
        from material_drawing_generator.drawing import _converter_root
        from material_drawing_generator.material_drawing import prepare_material_job
        from material_drawing_generator.drawing import convert_dxf_to_dwg

        if not (_converter_root() / "dwgwrite.exe").exists():
            self.skipTest("LibreDWG converter unavailable")
        row = SectionRow(2, "梁" * 35, "大梁", position="中央", section="H-100", material="BCR295")
        sheet = make_sheet([row])
        workbook = WorkbookData("synthetic.xlsx", [sheet])
        template = SiteTemplate.load(default_template_path())
        template.data.setdefault("material_symbols", {})["enabled"] = True
        _library, plans = prepare_material_job(workbook, _cjk_font_filename())
        plan = plans[sheet.name]
        base, reduced = required_width(sheet, 1.0, plan), required_width(sheet, 0.9, plan)
        left = float(template.data["dynamic_table_layout"].get("left", 16.0))
        template.data["dynamic_table_layout"].update({
            "right": left + (base + reduced) / 2,
            "auto_text_shrink_steps": 1,
        })
        with tempfile.TemporaryDirectory() as directory, patch(
            "material_drawing_generator.service.read_workbook", return_value=workbook
        ), patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)):
            result = generate_drawings(
                "synthetic.xlsx", Path(directory) / "out", template, {"dxf", "dwg"}, cad_output_scale=150
            )
            generated = result.sheets[0]
            self.assertEqual(generated.table_layout["actual_steps"], 1)
            final_path = Path(directory) / "dwg-roundtrip.dxf"
            converter = _converter_root() / "dwg2dxf.exe"
            output = Path(generated.files["dwg"])
            import subprocess
            completed = subprocess.run(
                [str(converter), "-y", "-o", str(final_path), str(output)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            doc = ezdxf.readfile(final_path)
            section = next(text for text in doc.modelspace().query("TEXT") if text.dxf.text == "H-100")
            self.assertAlmostEqual(section.dxf.height, 135.0, places=3)
            symbol = next(doc.modelspace().query("INSERT").__iter__())
            native_texts = [entity for entity in symbol.virtual_entities() if entity.dxftype() == "TEXT"]
            self.assertTrue(native_texts)
            self.assertTrue(all(abs(text.dxf.height - 135.0) < 1e-3 for text in native_texts))
            self.assertTrue(any(abs(text.dxf.insert.y - section.dxf.insert.y) < 1e-3 for text in native_texts))

    def test_default_materials_100_rows_fit_at_two_steps_and_dwgs_keep_layout(self):
        from material_drawing_generator.drawing import _converter_root
        if not (_converter_root() / "dwgwrite.exe").exists():
            self.skipTest("LibreDWG converter unavailable")
        rows = [
            SectionRow(i + 2, f"G{i}", "大梁", position="中央", section=f"H-{i + 100}",
                       material="SN400B" if i % 2 == 0 else "SN490B")
            for i in range(100)
        ]
        sheet = make_sheet(rows)
        workbook = WorkbookData("synthetic.xlsx", [sheet])
        template = SiteTemplate.load(default_template_path())
        template.data.setdefault("material_symbols", {})["enabled"] = True
        template.data["dynamic_table_layout"].update({"right": 66, "auto_text_shrink_steps": 2})
        for requested_steps in (0, 1):
            rejected_template = SiteTemplate.load(default_template_path())
            rejected_template.data.setdefault("material_symbols", {})["enabled"] = True
            rejected_template.data["dynamic_table_layout"].update({"right": 66, "auto_text_shrink_steps": requested_steps})
            with tempfile.TemporaryDirectory(dir=ROOT / ".test-temp" / "shrink-dev") as rejected_directory:
                rejected_output = Path(rejected_directory) / "must-not-create"
                with patch("material_drawing_generator.service.read_workbook", return_value=workbook), patch(
                    "material_drawing_generator.service.apply_submission_highlighting",
                    side_effect=AssertionError("Excel must remain untouched when policy does not fit"),
                ):
                    with self.assertRaises(DrawingError):
                        generate_drawings("synthetic.xlsx", rejected_output, rejected_template, {"dxf", "dwg"})
                self.assertFalse(rejected_output.exists())
        with tempfile.TemporaryDirectory() as directory, patch(
            "material_drawing_generator.service.read_workbook", return_value=workbook
        ), patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)):
            result = generate_drawings(
                "synthetic.xlsx", Path(directory) / "out", template, {"pdf", "dxf", "dwg"}, cad_output_scale=150
            )
            generated = result.sheets[0]
            self.assertEqual(generated.output_rows, 100)
            self.assertEqual(generated.table_layout["actual_steps"], 2)
            self.assertEqual(generated.table_layout["actual_text_height"], 0.8)
            self.assertEqual(generated.table_layout["actual_row_height"], 2.0)
            self.assertTrue(any("80%" in warning for warning in generated.warnings))
            report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
            self.assertEqual(report["material_symbols"]["2F"]["verified_rows"], 100)
            roundtrip = Path(directory) / "roundtrip.dxf"
            converted = subprocess.run(
                [str(_converter_root() / "dwg2dxf.exe"), "-y", "-o", str(roundtrip), generated.files["dwg"]],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(converted.returncode, 0, converted.stdout + converted.stderr)
            doc = ezdxf.readfile(roundtrip)
            inserts = list(doc.modelspace().query("INSERT"))
            self.assertEqual(len(inserts), 100)
            texts = list(doc.modelspace().query("TEXT"))
            self.assertEqual(tuple(doc.modelspace().dxf.limmax)[:2], (63000, 44550))
            section_texts = [text for text in texts if text.dxf.text.startswith("H-")]
            self.assertEqual(len(section_texts), 100)
            self.assertTrue(all(abs(text.dxf.height - 120.0) < 1e-3 for text in section_texts))
            for insert in inserts:
                native = [entity for entity in insert.virtual_entities() if entity.dxftype() == "TEXT"]
                self.assertTrue(native)
                self.assertTrue(all(abs(entity.dxf.height - 120.0) < 1e-3 for entity in native))
                self.assertTrue(any(
                    abs(entity.dxf.insert.y - section.dxf.insert.y) < 1e-3
                    for entity in native for section in section_texts
                ))

    def test_user_settings_and_site_template_policy_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(UserSettings, "path", return_value=root / "settings.json"):
                (root / "settings.json").write_text(json.dumps({"excel_path": "legacy.xlsx"}), encoding="utf-8")
                self.assertEqual(UserSettings.load().auto_text_shrink_steps, 0)
                settings = UserSettings(auto_text_shrink_steps=2)
                settings.save()
                self.assertEqual(UserSettings.load().auto_text_shrink_steps, 2)
            store = SiteTemplateStore("zh", path=root / "sites.json")
            layout = SiteTemplate.load(default_template_path()).data
            layout["dynamic_table_layout"]["auto_text_shrink_steps"] = 1
            store.save("Policy", layout, {"auto_text_shrink_steps": 1})
            loaded = store.get("Policy")
            self.assertEqual(loaded["layout"]["dynamic_table_layout"]["auto_text_shrink_steps"], 1)
            self.assertEqual(loaded["ui"]["auto_text_shrink_steps"], 1)

    def test_gui_policy_selection_persists_in_zh_and_ja_without_clipping(self):
        from material_drawing_generator.gui import MaterialDrawingApp

        self.assertIn("空间不足", translator("zh")("auto_text_shrink_1"))
        self.assertIn("空間不足", translator("ja")("auto_text_shrink_1"))
        self.assertIn("{details}", translator("zh")("generated_with_text_shrink"))
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"TEMP": directory, "TMP": directory, "LOCALAPPDATA": directory, "APPDATA": directory}
        ), patch("material_drawing_generator.settings.app_data_root", return_value=Path(directory)), patch(
            "material_drawing_generator.site_template_store.app_data_root", return_value=Path(directory)
        ):
            roots = []
            for language in ("zh", "ja"):
                try:
                    root = tk.Tk()
                    root.withdraw()
                except tk.TclError as exc:
                    self.skipTest(f"Tk display unavailable: {exc}")
                roots.append(root)
                try:
                    root.geometry("920x700")
                    app = MaterialDrawingApp(root, language=language)
                    root.update_idletasks()
                    self.assertEqual(len(app.auto_text_shrink_combo.cget("values")), 3)
                    row = app.auto_text_shrink_combo.master
                    for widget in (app.cad_output_scale_label, app.cad_output_scale_combo,
                                   app.cad_output_scale_note, app.auto_text_shrink_label,
                                   app.auto_text_shrink_combo):
                        self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), row.winfo_width())
                    app.auto_text_shrink_var.set(app._auto_text_shrink_label(2))
                    app._save_settings()
                    app.template_name_var.set("TextPolicy")
                    with patch("material_drawing_generator.gui.messagebox.askyesno", return_value=True):
                        app._save_site_template()
                    record = app.template_store.get("TextPolicy")
                    self.assertEqual(record["layout"]["dynamic_table_layout"]["auto_text_shrink_steps"], 2)
                    self.assertEqual(record["ui"]["auto_text_shrink_steps"], 2)
                    app.auto_text_shrink_var.set(app._auto_text_shrink_label(0))
                    app._load_site_template()
                    self.assertEqual(app._selected_auto_text_shrink_steps(), 2)
                    self.assertEqual(UserSettings.load().auto_text_shrink_steps, 2)
                finally:
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
