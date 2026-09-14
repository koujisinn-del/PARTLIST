from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from material_drawing_generator.gui import run_gui
from material_drawing_generator.service import generate_drawings, preview_excel
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.runtime_paths import dwg_tools_available


def build_parser(default_language: str = "zh") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Excel 部材表 → A3 PDF/DXF/DWG 图纸")
    parser.add_argument("--input", help="输入 .xlsx/.xlsm 文件")
    parser.add_argument("--output", help="输出文件夹")
    parser.add_argument("--lang", choices=("zh", "ja"), default=default_language, help="界面语言")
    parser.add_argument("--template", default="", help="现场模板 JSON")
    parser.add_argument("--formats", default="pdf,dxf,dwg" if dwg_tools_available() else "pdf,dxf", help="pdf,dxf,dwg")
    parser.add_argument("--version", default="", help="图纸版本号")
    parser.add_argument("--date", default=date.today().isoformat(), help="图纸日期")
    parser.add_argument("--descending", action="store_true", help="部件名称降序")
    parser.add_argument("--preview", action="store_true", help="只读取并显示统计，不生成")
    parser.add_argument("--material-symbols", action="store_true", help="启用基础材质库及自定义符号（试验）")
    return parser


def main(default_language: str | None = None):
    if default_language is None:
        config = Path(__file__).with_name("distribution.json")
        default_language = json.loads(config.read_text(encoding="utf-8"))["language"] if config.exists() else "zh"
    parser = build_parser(default_language)
    args = parser.parse_args()
    if not args.input:
        run_gui(args.lang, material_symbols=True if args.material_symbols else None)
        return
    template_path = args.template or str(default_template_path(args.lang))
    template = SiteTemplate.load(template_path)
    if args.material_symbols:
        template.data.setdefault("material_symbols", {})["enabled"] = True
    if args.preview:
        _, summaries = preview_excel(args.input, template, descending=args.descending)
        for summary in summaries:
            print(
                f"{summary.name}: {summary.rows} rows, {summary.groups} members, "
                f"group sizes={summary.group_sizes}"
            )
        return
    if not args.output:
        parser.error("使用命令行生成时必须指定 --output")
    result = generate_drawings(
        args.input,
        args.output,
        template,
        formats={item.strip() for item in args.formats.split(",") if item.strip()},
        version=args.version,
        drawing_date=args.date,
        descending=args.descending,
    )
    print(f"Generated {len(result.sheets)} drawings in {Path(args.output).resolve()}")
    print(result.report_path)


if __name__ == "__main__":
    main()
