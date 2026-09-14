"""Reproducible real-file check; previews are drawn from exported DWG readback."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf

from material_drawing_generator.cad_integrity import write_compatibility_dxf, validate_roundtrip
from material_drawing_generator.drawing import _converter_root
from material_drawing_generator.service import generate_drawings
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.text_pdf import TextPdfBook


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", type=Path, required=True)
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    before = {str(p): digest(p) for p in (args.excel, args.frame)}
    template = SiteTemplate.load(default_template_path()).with_frame_dwg(args.frame.resolve())
    result = generate_drawings(args.excel, args.output, template, {"pdf", "dwg"})
    args.scratch.mkdir(parents=True, exist_ok=True)
    book = TextPdfBook(args.scratch / "DWG回读预览.pdf", 420, 297)
    checks = []
    for sheet in result.sheets:
        dwg = Path(sheet.files["dwg"])
        source = Path(sheet.files["dxf_compatibility_backup"])
        compat = args.scratch / f"{sheet.name}_compat.dxf"
        reread = args.scratch / f"{sheet.name}_readback.dxf"
        write_compatibility_dxf(source, compat)
        process = subprocess.run([str(_converter_root() / "dwg2dxf.exe"), "-y", "-o", str(reread), str(dwg)],
                                 capture_output=True, check=True)
        verified = validate_roundtrip(compat, reread)
        document = ezdxf.readfile(reread)
        generated = [e for e in document.modelspace().query("TEXT") if e.dxf.style == "MDG_CJK"]
        zero_anchor = [e.dxf.text for e in generated if e.dxf.insert == (0, 0, 0)]
        assert not zero_anchor, zero_anchor
        assert all(e.dxf.halign == 0 and e.dxf.valign == 0 for e in generated)
        styles = [{"name": s.dxf.name, "font": s.dxf.font, "bigfont": s.dxf.bigfont,
                   "font_data": [(t.code, t.value) for t in s.get_xdata("ACAD")] if s.has_xdata("ACAD") else []}
                  for s in document.styles]
        checks.append({"sheet": sheet.name, **verified, "generated_text": len(generated),
                       "origin_alignment_errors": len(zero_anchor), "styles": styles,
                       "entity_types": dict(Counter(e.dxftype() for e in document.modelspace())),
                       "dwg_sha256": digest(dwg)})
        book.add_cad_page(document, sheet.name)
    book.save()
    assert before == {str(p): digest(p) for p in (args.excel, args.frame)}, "Input files were modified"
    payload = {"checks": checks, "unchanged_source_sha256": before,
               "scope": "DWG readback, effective text alignment, fonts and entity audit. Not an AutoCAD LT open test."}
    (args.output / "文字定位核验.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
