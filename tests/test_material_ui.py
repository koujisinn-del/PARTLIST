"""The material setting must control real templates and survive normal startup."""
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from material_drawing_generator.gui import MaterialDrawingApp
from material_drawing_generator.settings import UserSettings


class MaterialUiTests(unittest.TestCase):
    def test_single_window_toggle_site_save_load_and_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"LOCALAPPDATA": folder, "MDG_MATERIAL_TRIAL": "1"}):
            root = tk.Tk()
            root.withdraw()
            try:
                app = MaterialDrawingApp(root)
                self.assertFalse(app._template().material_symbols_enabled)
                app.material_symbols_var.set(True)
                self.assertTrue(app._template().material_symbols_enabled)
                app.template_name_var.set("材质配置测试")
                app._save_site_template()
                record = app.template_store.get("材质配置测试")
                self.assertTrue(record["layout"]["material_symbols"]["enabled"])
                self.assertTrue(record["ui"]["material_symbols"])
                app.material_symbols_var.set(False)
                self.assertFalse(app._template().material_symbols_enabled)
                app._load_site_template()
                self.assertTrue(app.material_symbols_var.get())
                self.assertTrue(UserSettings.load().material_symbols)
                app.template_name_var.set(app.template_store.default_name)
                app._load_site_template()
                self.assertFalse(app.material_symbols_var.get())
                app.material_symbols_var.set(True)
                app._save_settings()
                self.assertNotIn("MaterialTrial", str(UserSettings.path()))
                root.update_idletasks()
                self.assertLess(app.material_checkbox.master.winfo_reqwidth(), 888)
            finally:
                root.destroy()
            root = tk.Tk()
            root.withdraw()
            try:
                app = MaterialDrawingApp(root, language="ja")
                self.assertTrue(app.material_symbols_var.get())
                app.material_symbols_var.set(False)
                self.assertFalse(app._template().material_symbols_enabled)
                self.assertEqual(root.title(), app.tr("app_title"))
                # Review library content within the same app without opening
                # an on-screen window during the test.
                dialog = tk.Toplevel(root)
                dialog.withdraw()
                with patch("material_drawing_generator.gui.tk.Toplevel", return_value=dialog):
                    app._show_material_library()
                frame = next(iter(dialog.children.values()))
                from material_drawing_generator.material_library_view import MaterialLibraryView
                legends = [widget for widget in frame.children.values() if isinstance(widget, MaterialLibraryView)]
                self.assertEqual(len(legends), 1)
                legend = legends[0]
                self.assertGreaterEqual(len(legend.rules), 16)
                self.assertEqual(legend.headers, (app.tr("material_material"), app.tr("material_symbol")))
                image_count = sum(legend.body.type(item) == "image" for item in legend.body.find_all())
                expected = sum(not rule.is_unmarked for rule in legend.rules)
                self.assertEqual(image_count, expected)
                texts = [legend.body.itemcget(item, "text") for item in legend.body.find_all() if legend.body.type(item) == "text"]
                self.assertEqual(texts.count(app.tr("material_unmarked")), 2)
            finally:
                root.destroy()

    def test_cli_material_switch_updates_template_without_environment_forcing(self):
        import main
        with patch("sys.argv", ["main.py", "--material-symbols", "--input", "dummy.xlsx", "--preview"]), patch("main.preview_excel", return_value=(None, [])) as preview:
            main.main("zh")
            self.assertTrue(preview.call_args.args[1].material_symbols_enabled)
        with patch("sys.argv", ["main.py", "--material-symbols"]), patch("main.run_gui") as run:
            main.main("zh")
            run.assert_called_once_with("zh", material_symbols=True)
