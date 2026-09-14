"""Read-only source-data regression run for the supplied old/new CAD frames."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LOCALAPPDATA"] = str(ROOT / "tmp" / "frame_position_check")

from material_drawing_generator.drawing import (
    DrawingMetadata, convert_dxf_to_dwg, render_cad_pdf, render_dxf, _load_ezdxf,
)
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.xlsx_reader import read_workbook


def verify() -> None:
    excel = ROOT / "テスト用部材データ0904.xlsx"
    source_hash = hashlib.sha256(excel.read_bytes()).hexdigest()
    base_template = SiteTemplate.load(default_template_path())
    workbook = read_workbook(excel, base_template)
    sheet = next(item for item in workbook.sheets if item.name == "3Fリスト")
    record = workbook.drawing_list.record_for_sheet(sheet.name)
    assert record is not None
    metadata = DrawingMetadata(
        record.drawing_number, record.latest_version, record.latest_date, record.revision_dates,
    )
    output = ROOT / "output" / "position_fix_20260904"
    output.mkdir(parents=True, exist_ok=True)
    report = {"sheet": sheet.name, "number": metadata.drawing_number,
              "version": metadata.version, "date": metadata.drawing_date, "frames": []}
    for label, filename in (("旧图框", "図面テンプレート.dwg"),
                            ("文字标记图框", "図面テンプレート_テキスト枠.dwg")):
        frame = ROOT / filename
        frame_hash = hashlib.sha256(frame.read_bytes()).hexdigest()
        template = base_template.with_frame_dwg(frame)
        dxf_path = output / f"3Fリスト_{label}定位修正.dxf"
        count = render_dxf(dxf_path, sheet, template, metadata)
        assert count == sheet.source_row_count
        document = _load_ezdxf().readfile(dxf_path)
        texts = list(document.modelspace().query("TEXT"))
        values = [entity.dxf.text for entity in texts]
        for marker in ("図面名称", "図面番号", "M5FL　部材リスト", "R-", "R-X", "20xx/xx/xx", "20XX/XX/XX"):
            assert marker not in values, (label, marker)
        assert values.count(sheet.name) == 1
        assert values.count(metadata.drawing_number) == 1
        version = f"R-{metadata.version}"
        assert values.count(version) == 1
        positions = {entity.dxf.text: list(entity.dxf.insert)[:2] for entity in texts
                     if entity.dxf.text in (sheet.name, metadata.drawing_number, version)}
        assert positions[sheet.name][0] > 330 and positions[sheet.name][1] < 20
        assert positions[metadata.drawing_number][0] > 390
        assert positions[version][1] < 12
        history = sorted((entity.dxf.insert.y, entity.dxf.text) for entity in texts
                         if entity.dxf.insert.x > 407)
        assert len(history) == len(record.revision_dates)
        assert history[0][0] < 10
        report["frames"].append({"frame": filename, "rows": count, "positions": positions,
                                 "history": history, "dxf": str(dxf_path)})
        if label == "旧图框":
            pdf = ROOT / "output" / "pdf" / "3Fリスト_旧图框定位修正.pdf"
            pdf.parent.mkdir(parents=True, exist_ok=True)
            render_cad_pdf(dxf_path, pdf, template)
            convert_dxf_to_dwg(dxf_path, dxf_path.with_suffix(".dwg"))
            report["pdf"] = str(pdf)
        assert hashlib.sha256(frame.read_bytes()).hexdigest() == frame_hash
    assert hashlib.sha256(excel.read_bytes()).hexdigest() == source_hash
    (output / "定位核对.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    verify()
