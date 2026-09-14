from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from material_drawing_generator.settings import UserSettings
from material_drawing_generator.runtime_paths import dwg_tools_available
from material_drawing_generator.service import generate_drawings
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.drawing import prepare_frame_dxf


class SourceDistributionTests(unittest.TestCase):
    def test_no_converter_defaults_to_python_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("material_drawing_generator.runtime_paths.resource_root", return_value=Path(folder)):
                self.assertFalse(dwg_tools_available())
                self.assertFalse(UserSettings().output_dwg)
                self.assertTrue(UserSettings().output_pdf)
                self.assertTrue(UserSettings().output_dxf)

    def test_missing_converter_stops_before_reading_or_editing_workbook(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("material_drawing_generator.service.dwg_tools_available", return_value=False), patch(
                "material_drawing_generator.service.read_workbook", side_effect=AssertionError("must not read")
            ):
                with self.assertRaisesRegex(ValueError, "尚未开始生成或修改 Excel"):
                    generate_drawings("not-opened.xlsx", folder, SiteTemplate.load(default_template_path()), {"pdf", "dwg"})

    def test_dxf_frame_does_not_launch_converter(self):
        with tempfile.TemporaryDirectory() as folder:
            frame = Path(folder) / "frame.dxf"
            frame.touch()
            with patch("material_drawing_generator.drawing.subprocess.run", side_effect=AssertionError("no executable")):
                self.assertEqual(prepare_frame_dxf(frame), frame.resolve())
