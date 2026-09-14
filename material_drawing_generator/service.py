from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .drawing import (
    DrawingMetadata,
    convert_dxf_to_dwg,
    render_cad_pdf,
    render_dxf,
    render_pdf,
    safe_filename,
    _cjk_font_filename,
)
from .excel_marker import apply_submission_highlighting
from .models import WorkbookData
from .template import SiteTemplate
from .xlsx_reader import read_workbook
from .app_logging import get_logger
from .text_pdf import TextPdfBook
from .sheet_print import add_drawing_list_page
from .runtime_paths import dwg_tools_available


logger = get_logger()


@dataclass(slots=True)
class SheetSummary:
    name: str
    rows: int
    groups: int
    categories: dict[str, int]
    group_sizes: list[int]


@dataclass(slots=True)
class GeneratedSheet:
    name: str
    input_rows: int
    output_rows: int
    drawing_number: str = ""
    version: str = ""
    drawing_date: str = ""
    files: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    pdf_page: int | None = None


@dataclass(slots=True)
class GenerationResult:
    source_excel: str
    template: str
    sheets: list[GeneratedSheet]
    report_path: str
    skipped_sheets: list[str]
    pdf_path: str = ""


def summarize(workbook: WorkbookData) -> list[SheetSummary]:
    summaries: list[SheetSummary] = []
    for sheet in workbook.sheets:
        categories: dict[str, int] = {}
        for row in sheet.source_rows:
            categories[row.category] = categories.get(row.category, 0) + 1
        summaries.append(
            SheetSummary(
                name=sheet.name,
                rows=sheet.source_row_count,
                groups=len(sheet.groups),
                categories=categories,
                group_sizes=[len(group.rows) for group in sheet.groups],
            )
        )
    return summaries


def preview_excel(
    excel_path: str | Path,
    template: SiteTemplate,
    descending: bool = False,
) -> tuple[WorkbookData, list[SheetSummary]]:
    logger.info("读取 Excel：%s", Path(excel_path).resolve())
    workbook = read_workbook(excel_path, template, descending=descending)
    logger.info("Excel 读取完成：%d 张部材表图纸", len(workbook.sheets))
    return workbook, summarize(workbook)


def generate_drawings(
    excel_path: str | Path,
    output_directory: str | Path,
    template: SiteTemplate,
    formats: set[str],
    version: str = "",
    drawing_date: str | None = None,
    revision_dates: list[str] | None = None,
    descending: bool = False,
) -> GenerationResult:
    logger.info(
        "开始生成图纸：Excel=%s，模板=%s，格式=%s",
        Path(excel_path).resolve(),
        template.name,
        ",".join(sorted(formats)),
    )
    requested = {fmt.lower() for fmt in formats}
    invalid = requested - {"pdf", "dxf", "dwg"}
    if invalid:
        raise ValueError(f"不支持的输出格式：{', '.join(sorted(invalid))}")
    if not requested:
        raise ValueError("请至少选择一种输出格式。")
    if "dwg" in requested and not dwg_tools_available():
        raise ValueError("当前程序目录缺少 DWG 转换组件，请补齐组件后生成 DWG；PDF/DXF 可独立使用。尚未开始生成或修改 Excel。")
    workbook = read_workbook(excel_path, template, descending=descending)
    material_plans = None
    if template.material_symbols_enabled:
        from .material_drawing import prepare_material_job
        from .drawing import _prepare_tables
        _library, material_plans = prepare_material_job(workbook, _cjk_font_filename())
        for sheet in workbook.sheets:
            _prepare_tables(sheet, template, material_plans[sheet.name])
    output_root = Path(output_directory).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    highlight_result = apply_submission_highlighting(excel_path, workbook.drawing_list)
    logger.info("Excel 标红：%s", highlight_result.message)
    with tempfile.TemporaryDirectory(prefix="._drawing_job_", dir=output_root) as staging:
        return _generate_files(
            excel_path, output_root, template, requested, workbook, highlight_result,
            version, drawing_date, revision_dates, Path(staging), material_plans,
        )


def _generate_files(excel_path, output_root, template, requested, workbook, highlight_result,
                    version, drawing_date, revision_dates, staging, material_plans=None) -> GenerationResult:
    material_reports = {}
    pending_publication = []
    combined_path = output_root / f"{safe_filename(Path(excel_path).stem)}_图纸.pdf"
    book = None
    if "pdf" in requested:
        book = TextPdfBook(staging / "combined.pdf", float(template.paper["width"]), float(template.paper["height"]))
        if workbook.drawing_list is not None:
            add_drawing_list_page(
                book, Path(excel_path), workbook.drawing_list.sheet_name, _cjk_font_filename(),
                [column for _, column in workbook.drawing_list.version_columns],
                min((record.source_row for record in workbook.drawing_list.records), default=1),
            )
    generated: list[GeneratedSheet] = []
    for sheet in workbook.sheets:
        record = (
            workbook.drawing_list.record_for_sheet(sheet.name)
            if workbook.drawing_list is not None
            else None
        )
        if workbook.drawing_list is not None and record is None:
            raise ValueError(f"图纸列表中找不到与工作表“{sheet.name}”对应的图纸名称。")
        resolved_version = record.latest_version if record and record.latest_version else version
        resolved_date = (
            record.latest_date
            if record and record.latest_date
            else drawing_date or date.today().isoformat()
        )
        resolved_revisions = (
            record.revision_dates
            if record and record.revision_dates
            else revision_dates or [resolved_date]
        )
        metadata = DrawingMetadata(
            drawing_number=record.drawing_number if record else "",
            version=resolved_version,
            drawing_date=resolved_date,
            revision_dates=resolved_revisions,
        )
        base_name = safe_filename(sheet.name)
        sheet_result = GeneratedSheet(
            name=sheet.name,
            input_rows=sheet.source_row_count,
            output_rows=0,
            drawing_number=metadata.drawing_number,
            version=metadata.version,
            drawing_date=metadata.drawing_date,
            warnings=list(sheet.warnings),
        )
        if highlight_result.message and not highlight_result.applied:
            sheet_result.warnings.append(highlight_result.message)
        if requested:
            destination_dxf = output_root / f"{base_name}.dxf"
            dxf_path = (staging if material_plans is not None else output_root) / f"{base_name}.dxf"
            plan = material_plans[sheet.name] if material_plans is not None else None
            cad_rows = render_dxf(dxf_path, sheet, template, metadata, plan)
            if plan is not None:
                from .material_drawing import verify_material_cells
                from .drawing import _load_ezdxf
                material_reports[sheet.name] = verify_material_cells(_load_ezdxf().readfile(dxf_path), plan)
            if sheet_result.output_rows and cad_rows != sheet_result.output_rows:
                raise ValueError(f"工作表“{sheet.name}”PDF 与 CAD 输出行数不一致。")
            sheet_result.output_rows = cad_rows
            if book is not None:
                from .drawing import _load_ezdxf
                book.add_cad_page(_load_ezdxf().readfile(dxf_path), sheet.name)
                sheet_result.files["pdf"] = str(combined_path)
                sheet_result.pdf_page = len(book.page_names)
            if "dxf" in requested:
                sheet_result.files["dxf"] = str(destination_dxf)
            if "dwg" in requested:
                destination_dwg = output_root / f"{base_name}.dwg"
                dwg_path = (staging if plan is not None else output_root) / f"{base_name}.dwg"
                converter_messages = convert_dxf_to_dwg(dxf_path, dwg_path)
                sheet_result.files["dwg"] = str(destination_dwg)
                if plan is not None:
                    pending_publication.append((dwg_path, destination_dwg))
                if converter_messages:
                    sheet_result.warnings.append(
                        "DWG 转换器返回了兼容性提示；已同时保留 DXF 供核对。"
                    )
                if "dxf" not in requested:
                    # Keep DXF as a recoverable compatibility source even when only DWG was requested.
                    sheet_result.files["dxf_compatibility_backup"] = str(destination_dxf)
            elif "dxf" not in requested:
                dxf_path.unlink(missing_ok=True)
            if plan is not None and dxf_path.exists():
                pending_publication.append((dxf_path, destination_dxf))
        if sheet_result.output_rows != sheet_result.input_rows:
            raise ValueError(
                f"工作表“{sheet.name}”最终对账失败：读取 {sheet_result.input_rows} 行，"
                f"输出 {sheet_result.output_rows} 行。"
            )
        generated.append(sheet_result)
        logger.info(
            "图纸完成：%s，读取=%d，绘制=%d，输出=%s",
            sheet.name,
            sheet_result.input_rows,
            sheet_result.output_rows,
            ",".join(sorted(sheet_result.files)),
        )
    report_path = output_root / "生成报告.json"
    if book is not None:
        book.save()
        book.path.replace(combined_path)
    for staged, destination in pending_publication:
        staged.replace(destination)
    report_payload = {
        "source_excel": str(Path(excel_path).resolve()),
        "template": template.name,
        "version_source": "图纸列表" if workbook.drawing_list is not None else "软件输入",
        "excel_highlighting": asdict(highlight_result),
        "skipped_hidden_sheets": workbook.skipped_sheets,
        "sheets": [asdict(item) for item in generated],
        "pdf_path": str(combined_path) if book is not None else "",
        "pdf_pages": book.page_names if book is not None else [],
        "material_symbols": material_reports,
    }
    report_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("全部生成完成：%d 张，报告=%s", len(generated), report_path)
    return GenerationResult(
        source_excel=str(Path(excel_path).resolve()),
        template=template.name,
        sheets=generated,
        report_path=str(report_path),
        skipped_sheets=workbook.skipped_sheets,
        pdf_path=str(combined_path) if book is not None else "",
    )
