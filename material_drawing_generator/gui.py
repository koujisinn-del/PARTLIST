from __future__ import annotations

import copy
import math
import os
import queue
import threading
import traceback
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from .app_logging import get_logger, log_directory
from .i18n import translator
from .models import PreviewCellStyle, PreviewSheet, WorkbookPreview
from .models import normalized_text
from .service import generate_drawings
from .settings import UserSettings
from .site_template_store import SiteTemplateStore
from .template import SiteTemplate, default_template_path
from .xlsx_reader import read_workbook, read_workbook_preview


logger = get_logger()


def _column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


class SpreadsheetPreview(ttk.Frame):
    MAX_ROWS = 500
    MAX_COLUMNS = 40
    ROW_HEIGHT = 24
    HEADER_HEIGHT = 28
    ROW_HEADER_WIDTH = 56

    def __init__(self, parent, tr):
        super().__init__(parent)
        self.tr = tr
        self.workbook: WorkbookPreview | None = None
        self._content_width = 0
        self._content_height = 0
        self._font_cache: dict[tuple[str, int, bool, bool], tkfont.Font] = {}
        self.sheet_var = tk.StringVar()
        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(0, 5))
        ttk.Label(controls, text=self.tr("sheet")).pack(side="left")
        self.sheet_combo = ttk.Combobox(
            controls,
            textvariable=self.sheet_var,
            state="readonly",
            width=32,
        )
        self.sheet_combo.pack(side="left", padx=8)
        self.sheet_combo.bind("<<ComboboxSelected>>", self._sheet_changed)
        self.limit_label = ttk.Label(controls, text="")
        self.limit_label.pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            body,
            background="white",
            highlightthickness=1,
            highlightbackground="#b8b8b8",
        )
        self.v_scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.h_scroll = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=self.v_scroll.set,
            xscrollcommand=self.h_scroll.set,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<MouseWheel>", self._mouse_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._shift_mouse_wheel)
        self.canvas.bind("<Configure>", self._canvas_resized)

    def clear(self):
        self.workbook = None
        self.sheet_combo.configure(values=())
        self.sheet_var.set("")
        self.canvas.delete("all")
        self.limit_label.configure(text="")
        self._content_width = 0
        self._content_height = 0
        self.h_scroll.grid_remove()

    def set_workbook(self, workbook: WorkbookPreview, preferred_sheet: str = ""):
        self.workbook = workbook
        names = [sheet.name for sheet in workbook.sheets]
        self.sheet_combo.configure(values=names)
        selected = preferred_sheet if preferred_sheet in names else (names[0] if names else "")
        self.sheet_var.set(selected)
        self.draw_selected_sheet()

    def selected_sheet_name(self) -> str:
        return self.sheet_var.get()

    def _sheet_changed(self, _event=None):
        self.draw_selected_sheet()

    def _mouse_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _shift_mouse_wheel(self, event):
        self.canvas.xview_scroll(int(-event.delta / 120), "units")

    def _canvas_resized(self, _event=None):
        self._update_scroll_region()

    def _update_scroll_region(self):
        viewport_width = max(1, self.canvas.winfo_width())
        region_width = max(self._content_width, viewport_width)
        self.canvas.configure(
            scrollregion=(0, 0, region_width, self._content_height)
        )
        if self._content_width <= viewport_width:
            self.h_scroll.grid_remove()
            self.canvas.xview_moveto(0)
        else:
            self.h_scroll.grid()

    def _sheet(self) -> PreviewSheet | None:
        if self.workbook is None:
            return None
        selected = self.sheet_var.get()
        return next((sheet for sheet in self.workbook.sheets if sheet.name == selected), None)

    def _cell_font(self, style: PreviewCellStyle | None) -> tkfont.Font:
        family = style.font_name if style and style.font_name else "Microsoft YaHei UI"
        size = max(6, min(24, round(style.font_size if style else 9.0)))
        bold = bool(style and style.bold)
        italic = bool(style and style.italic)
        key = (family, size, bold, italic)
        font = self._font_cache.get(key)
        if font is None:
            font = tkfont.Font(
                root=self,
                family=family,
                size=size,
                weight="bold" if bold else "normal",
                slant="italic" if italic else "roman",
            )
            self._font_cache[key] = font
        return font

    @staticmethod
    def _text_position(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        style: PreviewCellStyle | None,
        value: object,
    ) -> tuple[float, float, str, str]:
        horizontal = (style.horizontal if style else "").casefold()
        vertical = (style.vertical if style else "").casefold()
        if horizontal in {"center", "centercontinuous", "distributed", "justify"}:
            x = (x1 + x2) / 2
            horizontal_anchor = ""
            justify = "center"
        elif horizontal == "right" or (not horizontal and isinstance(value, (int, float))):
            x = x2 - 5
            horizontal_anchor = "e"
            justify = "right"
        else:
            x = x1 + 5
            horizontal_anchor = "w"
            justify = "left"

        if vertical == "top":
            y = y1 + 3
            vertical_anchor = "n"
        elif vertical in {"center", "distributed", "justify"}:
            y = (y1 + y2) / 2
            vertical_anchor = ""
        else:
            y = y2 - 3
            vertical_anchor = "s"

        anchor = vertical_anchor + horizontal_anchor
        return x, y, anchor or "center", justify

    def draw_selected_sheet(self):
        sheet = self._sheet()
        self.canvas.delete("all")
        if sheet is None:
            return
        shown_rows = sheet.rows[: self.MAX_ROWS]
        column_count = min(max(sheet.max_column, 1), self.MAX_COLUMNS)
        widths: list[int] = []
        for column in range(column_count):
            longest = len(_column_name(column))
            for _, values in shown_rows[:120]:
                value = str(values.get(column, ""))
                longest = max(longest, min(len(value), 24))
            widths.append(max(72, min(190, 14 + longest * 9)))

        x_positions = [self.ROW_HEADER_WIDTH]
        for width in widths:
            x_positions.append(x_positions[-1] + width)
        total_width = x_positions[-1]
        row_heights: list[int] = []
        for row_number, values in shown_rows:
            source_points = sheet.row_heights.get(row_number, sheet.default_row_height)
            source_pixels = max(20, math.ceil(source_points * 96 / 72))
            required_text_height = 20
            for column, value in values.items():
                line_count = str(value).count("\n") + 1
                font = self._cell_font(sheet.cell_styles.get((row_number, column)))
                required_text_height = max(
                    required_text_height,
                    line_count * int(font.metrics("linespace")) + 4,
                )
            # Tk and Excel use slightly different font metrics.  Keep the
            # source row height, with only the minimum extra pixel allowance
            # needed to keep an explicit Excel line break inside its border.
            row_heights.append(max(source_pixels, required_text_height))
        y_positions = [self.HEADER_HEIGHT]
        for height in row_heights:
            y_positions.append(y_positions[-1] + height)
        total_height = y_positions[-1]

        self.canvas.create_rectangle(
            0,
            0,
            self.ROW_HEADER_WIDTH,
            self.HEADER_HEIGHT,
            fill="#e9edf2",
            outline="#b8bec7",
        )
        for column in range(column_count):
            x1, x2 = x_positions[column], x_positions[column + 1]
            self.canvas.create_rectangle(
                x1,
                0,
                x2,
                self.HEADER_HEIGHT,
                fill="#e9edf2",
                outline="#b8bec7",
            )
            self.canvas.create_text(
                (x1 + x2) / 2,
                self.HEADER_HEIGHT / 2,
                text=_column_name(column),
                font=("Microsoft YaHei UI", 9, "bold"),
            )

        for visible_index, (row_number, values) in enumerate(shown_rows):
            y1 = y_positions[visible_index]
            y2 = y_positions[visible_index + 1]
            self.canvas.create_rectangle(
                0,
                y1,
                self.ROW_HEADER_WIDTH,
                y2,
                fill="#f1f3f5",
                outline="#c7cbd1",
            )
            self.canvas.create_text(
                self.ROW_HEADER_WIDTH - 7,
                (y1 + y2) / 2,
                text=str(row_number),
                anchor="e",
                fill="#555555",
                font=("Microsoft YaHei UI", 9),
            )
            for column in range(column_count):
                x1, x2 = x_positions[column], x_positions[column + 1]
                highlighted = (row_number, column) in sheet.highlighted_cells
                fill = "#ffc7ce" if highlighted else "#ffffff"
                foreground = "#9c0006" if highlighted else "#202020"
                self.canvas.create_rectangle(
                    x1,
                    y1,
                    x2,
                    y2,
                    fill=fill,
                    outline="#d8dadd",
                )
                value = str(values.get(column, ""))
                if len(value) > 40:
                    value = value[:39] + "…"
                style = sheet.cell_styles.get((row_number, column))
                text_x, text_y, anchor, justify = self._text_position(
                    x1, y1, x2, y2, style, values.get(column, "")
                )
                self.canvas.create_text(
                    text_x,
                    text_y,
                    text=value,
                    anchor=anchor,
                    justify=justify,
                    fill=foreground,
                    font=self._cell_font(style),
                )

        row_index_by_number = {
            row_number: index for index, (row_number, _) in enumerate(shown_rows)
        }
        values_by_row = {row_number: values for row_number, values in shown_rows}
        for start_row, start_column, end_row, end_column in sheet.merged_ranges:
            if start_column >= column_count or end_column >= column_count:
                continue
            start_index = row_index_by_number.get(start_row)
            end_index = row_index_by_number.get(end_row)
            if start_index is None or end_index is None:
                continue
            x1 = x_positions[start_column]
            x2 = x_positions[end_column + 1]
            y1 = y_positions[start_index]
            y2 = y_positions[end_index + 1]
            highlighted = any(
                (row_number, column) in sheet.highlighted_cells
                for row_number in range(start_row, end_row + 1)
                for column in range(start_column, end_column + 1)
            )
            fill = "#ffc7ce" if highlighted else "#ffffff"
            foreground = "#9c0006" if highlighted else "#202020"
            self.canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                fill=fill,
                outline="#d8dadd",
            )
            value = str(values_by_row.get(start_row, {}).get(start_column, ""))
            if len(value) > 60:
                value = value[:59] + "…"
            source_value = values_by_row.get(start_row, {}).get(start_column, "")
            style = sheet.cell_styles.get((start_row, start_column))
            text_x, text_y, anchor, justify = self._text_position(
                x1, y1, x2, y2, style, source_value
            )
            self.canvas.create_text(
                text_x,
                text_y,
                text=value,
                anchor=anchor,
                justify=justify,
                fill=foreground,
                font=self._cell_font(style),
                tags=("merged-text",),
            )

        self._content_width = total_width
        self._content_height = total_height
        self._update_scroll_region()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        if len(sheet.rows) > self.MAX_ROWS or sheet.max_column > self.MAX_COLUMNS:
            self.limit_label.configure(
                text=self.tr(
                    "rows_limited",
                    rows=min(len(sheet.rows), self.MAX_ROWS),
                    columns=column_count,
                )
            )
        else:
            self.limit_label.configure(text="")


class MaterialDrawingApp:
    def __init__(self, root: tk.Tk, language: str = "zh", material_symbols: bool | None = None):
        self.root = root
        self.language = language if language in {"zh", "ja"} else "zh"
        self.tr = translator(self.language)
        self.settings = UserSettings.load()
        self.template_store = SiteTemplateStore(self.language)
        self._busy = False
        self._thread_results: queue.Queue[tuple[str, object]] = queue.Queue()

        requested_template_name = self.settings.active_template_name
        if not requested_template_name or not self.template_store.exists(requested_template_name):
            template_names = self.template_store.names()
            requested_template_name = (
                template_names[0] if template_names else self.template_store.default_name
            )
        record = self.template_store.get(requested_template_name)
        if record is None:
            base_template = SiteTemplate.load(default_template_path(self.language))
            self._current_template_data = copy.deepcopy(base_template.data)
        else:
            self._current_template_data = copy.deepcopy(record["layout"])
        self.loaded_template_name = requested_template_name

        self.excel_var = tk.StringVar(value=self.settings.excel_path)
        self.output_var = tk.StringVar(value=self.settings.output_path)
        self.template_name_var = tk.StringVar(value=self.loaded_template_name)
        self.frame_dwg_var = tk.StringVar(value=self.settings.frame_dwg_path)
        self.sort_var = tk.StringVar(
            value=self.tr("descending") if self.settings.sort_descending else self.tr("ascending")
        )
        self.pdf_var = tk.BooleanVar(value=self.settings.output_pdf)
        self.dxf_var = tk.BooleanVar(value=self.settings.output_dxf)
        self.dwg_var = tk.BooleanVar(value=self.settings.output_dwg)
        self.material_symbols_var = tk.BooleanVar(
            value=self.settings.material_symbols if material_symbols is None else material_symbols
        )
        self.status_var = tk.StringVar(value=self.tr("ready"))

        self.root.title(self.tr("app_title"))
        self.root.geometry("1080x860")
        self.root.minsize(920, 700)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        logger.info("软件启动：language=%s", self.language)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(8, weight=1)

        ttk.Label(main, text=self.tr("app_title"), font=("Microsoft YaHei UI", 18, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(main, text=self.tr("subtitle")).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(2, 12)
        )
        ttk.Label(main, text=self.tr("site_template")).grid(row=2, column=0, sticky="w", pady=3)
        self.template_combo = ttk.Combobox(
            main,
            textvariable=self.template_name_var,
            values=self.template_store.names(),
            state="normal",
        )
        self.template_combo.grid(row=2, column=1, sticky="ew", padx=(8, 4))
        self.template_combo.bind("<FocusOut>", self._ensure_template_name)
        ttk.Button(main, text=self.tr("load_template"), command=self._load_site_template).grid(
            row=2, column=2, padx=4
        )
        ttk.Button(main, text=self.tr("save_template"), command=self._save_site_template).grid(
            row=2, column=3, padx=(4, 0)
        )

        self._path_row(main, 3, self.tr("excel_file"), self.excel_var, self._choose_excel)
        self._path_row(main, 4, self.tr("output_folder"), self.output_var, self._choose_output)
        self._path_row(main, 5, self.tr("frame_dwg"), self.frame_dwg_var, self._choose_dwg)

        settings = ttk.LabelFrame(main, text=self.tr("drawing_settings"), padding=10)
        settings.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(10, 8))
        ttk.Label(settings, text=self.tr("sort")).pack(side="left")
        ttk.Combobox(
            settings,
            textvariable=self.sort_var,
            values=(self.tr("ascending"), self.tr("descending")),
            state="readonly",
            width=9,
        ).pack(side="left", padx=(7, 22))
        ttk.Label(settings, text=self.tr("formats")).pack(side="left")
        ttk.Checkbutton(settings, text="PDF", variable=self.pdf_var).pack(side="left", padx=(8, 6))
        ttk.Checkbutton(settings, text="DXF", variable=self.dxf_var).pack(side="left", padx=6)
        ttk.Checkbutton(settings, text="DWG", variable=self.dwg_var).pack(side="left", padx=6)
        self.material_checkbox = ttk.Checkbutton(
            settings, text=self.tr("use_material_symbols"), variable=self.material_symbols_var,
            command=self._save_settings,
        )
        self.material_checkbox.pack(side="left", padx=(18, 6))
        ttk.Button(settings, text=self.tr("material_library"), command=self._show_material_library).pack(side="left", padx=6)

        actions = ttk.Frame(main)
        actions.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(2, 8))
        self.preview_button = ttk.Button(actions, text=self.tr("preview"), command=self._preview)
        self.preview_button.pack(side="left")
        self.generate_button = ttk.Button(actions, text=self.tr("generate"), command=self._generate)
        self.generate_button.pack(side="left", padx=8)
        ttk.Button(actions, text=self.tr("open_logs"), command=self._open_logs).pack(side="left")
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

        preview_box = ttk.LabelFrame(main, text=self.tr("preview_title"), padding=8)
        preview_box.grid(row=8, column=0, columnspan=4, sticky="nsew")
        self.preview = SpreadsheetPreview(preview_box, self.tr)
        self.preview.pack(fill="both", expand=True)

    def _path_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, columnspan=2, sticky="ew", padx=8
        )
        ttk.Button(parent, text=self.tr("select"), command=command).grid(row=row, column=3)

    def _choose_excel(self):
        selected = filedialog.askopenfilename(
            title=self.tr("choose_excel"),
            filetypes=[("Excel", "*.xlsx *.xlsm"), ("All", "*.*")],
        )
        if selected:
            self.excel_var.set(selected)
            if not self.output_var.get().strip():
                self.output_var.set(str(Path(selected).parent / f"{Path(selected).stem}_图纸输出"))
            self.status_var.set(self.tr("selected"))
            self._save_settings()

    def _choose_output(self):
        selected = filedialog.askdirectory(title=self.tr("choose_output"))
        if selected:
            self.output_var.set(selected)
            self._save_settings()

    def _choose_dwg(self):
        selected = filedialog.askopenfilename(
            title=self.tr("choose_dwg"),
            filetypes=[("CAD", "*.dwg *.dxf"), ("DWG", "*.dwg"), ("DXF", "*.dxf")],
        )
        if selected:
            self.frame_dwg_var.set(selected)
            self._save_settings()

    def _template(self) -> SiteTemplate:
        template = SiteTemplate(
            default_template_path(self.language),
            copy.deepcopy(self._current_template_data),
        )
        template.data.setdefault("material_symbols", {})["enabled"] = self.material_symbols_var.get()
        frame_path = self.frame_dwg_var.get().strip()
        return template.with_frame_dwg(frame_path) if frame_path else template

    def _current_site_ui_settings(self) -> dict[str, object]:
        return {
            "output_path": self.output_var.get().strip(),
            "frame_dwg_path": self.frame_dwg_var.get().strip(),
            "sort_descending": self.sort_var.get() == self.tr("descending"),
            "output_pdf": self.pdf_var.get(),
            "output_dxf": self.dxf_var.get(),
            "output_dwg": self.dwg_var.get(),
            "material_symbols": self.material_symbols_var.get(),
        }

    def _ensure_template_name(self, _event=None) -> str:
        name = normalized_text(self.template_name_var.get())
        if name:
            return name
        names = self.template_store.names()
        fallback = names[0] if names else self.template_store.default_name
        self.template_name_var.set(fallback)
        return fallback

    def _load_site_template(self):
        name = self._ensure_template_name()
        record = self.template_store.get(name)
        if record is None:
            messagebox.showerror(
                self.tr("error_title"),
                self.tr("template_not_found", name=name),
            )
            return
        self._current_template_data = copy.deepcopy(record["layout"])
        ui = record.get("ui", {})
        self.output_var.set(str(ui.get("output_path", "")))
        self.frame_dwg_var.set(str(ui.get("frame_dwg_path", "")))
        self.sort_var.set(
            self.tr("descending") if bool(ui.get("sort_descending", False)) else self.tr("ascending")
        )
        self.pdf_var.set(bool(ui.get("output_pdf", True)))
        self.dxf_var.set(bool(ui.get("output_dxf", True)))
        self.dwg_var.set(bool(ui.get("output_dwg", True)))
        self.material_symbols_var.set(bool(ui.get("material_symbols", self._current_template_data.get("material_symbols", {}).get("enabled", False))))
        self.loaded_template_name = name
        self.template_name_var.set(name)
        self.status_var.set(self.tr("template_loaded", name=name))
        self._save_settings()

    def _save_site_template(self):
        name = self._ensure_template_name()
        if self.template_store.exists(name):
            should_overwrite = messagebox.askyesno(
                self.tr("template_overwrite_title"),
                self.tr("template_overwrite", name=name),
            )
            if not should_overwrite:
                return
        self.template_store.save(
            name,
            self._template().data,
            self._current_site_ui_settings(),
        )
        saved = self.template_store.get(name)
        if saved is not None:
            self._current_template_data = copy.deepcopy(saved["layout"])
        self.loaded_template_name = name
        self.template_name_var.set(name)
        self.template_combo.configure(values=self.template_store.names())
        self.status_var.set(self.tr("template_saved", name=name))
        self._save_settings()

    def _input_path(self) -> Path:
        value = self.excel_var.get().strip()
        if not value:
            raise ValueError(self.tr("missing_excel"))
        return Path(value)

    def _show_material_library(self):
        from .materials import MaterialLibrary
        from .drawing import _cjk_font_filename
        from .material_library_view import MaterialLibraryView
        try:
            library = MaterialLibrary.load()
        except ValueError as exc:
            messagebox.showerror(self.tr("error_title"), str(exc), parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title(self.tr("material_library"))
        height = max(320, min(760, len(library.rules) * MaterialLibraryView.ROW_HEIGHT + 100, self.root.winfo_screenheight() - 100))
        window.geometry(f"680x{height}")
        window.minsize(440, 280)
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=self.tr("material_library_note"), wraplength=640).pack(anchor="w", pady=(0, 10))
        try:
            legend = MaterialLibraryView(frame, library, _cjk_font_filename(), self.tr)
        except Exception as exc:
            window.destroy()
            messagebox.showerror(self.tr("error_title"), str(exc), parent=self.root)
            return
        legend.pack(fill="both", expand=True)

    def _output_path(self) -> Path:
        value = self.output_var.get().strip()
        if not value:
            raise ValueError(self.tr("missing_output"))
        return Path(value)

    def _formats(self) -> set[str]:
        result: set[str] = set()
        if self.pdf_var.get():
            result.add("pdf")
        if self.dxf_var.get():
            result.add("dxf")
        if self.dwg_var.get():
            result.add("dwg")
        if not result:
            raise ValueError(self.tr("missing_format"))
        return result

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.preview_button.configure(state=state)
        self.generate_button.configure(state=state)

    def _run_thread(self, target):
        if self._busy:
            return
        self._set_busy(True)

        def runner():
            try:
                result = target()
                self._thread_results.put(("success", result))
            except Exception as exc:
                detail = traceback.format_exc()
                logger.error("操作失败：%s\n%s", exc, detail)
                self._thread_results.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()
        self.root.after(25, self._poll_thread_result)

    def _poll_thread_result(self):
        try:
            state, result = self._thread_results.get_nowait()
        except queue.Empty:
            if self._busy:
                self.root.after(25, self._poll_thread_result)
            return
        if state == "success":
            self._operation_success(result)
        else:
            self._operation_failed(result)

    def _preview(self):
        try:
            excel_path = self._input_path().resolve()
            template = self._template()
        except Exception as exc:
            self._operation_failed(exc)
            return
        self._save_settings()
        self.status_var.set(self.tr("reading"))

        def operation():
            preview = read_workbook_preview(excel_path, template)
            drawing_count = len(preview.sheets) - (1 if preview.drawing_list is not None else 0)
            message = self.tr(
                "preview_loaded",
                sheets=len(preview.sheets),
                drawings=max(0, drawing_count),
            )
            return {"kind": "preview", "preview": preview, "message": message}

        self._run_thread(operation)

    def _generate(self):
        try:
            excel_path = self._input_path().resolve()
            output_path = self._output_path().resolve()
            template = self._template()
            formats = self._formats()
            descending = self.sort_var.get() == self.tr("descending")
        except Exception as exc:
            self._operation_failed(exc)
            return
        self._save_settings()
        self.status_var.set(self.tr("reading_categories"))

        def operation():
            workbook = read_workbook(excel_path, template, descending=descending)
            plans = [(sheet.name, sheet.categories) for sheet in workbook.sheets]
            return {
                "kind": "generation_confirmation",
                "plans": plans,
                "excel_path": excel_path,
                "output_path": output_path,
                "template": template,
                "formats": formats,
                "descending": descending,
            }

        self._run_thread(operation)

    def _confirm_generation(self, request):
        plans = request["plans"]
        details = "\n".join(
            self.tr(
                "confirm_tables_sheet",
                sheet=sheet_name,
                count=len(categories),
                categories="、".join(categories),
            )
            for sheet_name, categories in plans
        )
        total = sum(len(categories) for _, categories in plans)
        confirmed = messagebox.askyesno(
            self.tr("confirm_tables_title"),
            self.tr("confirm_tables_message", details=details, total=total),
        )
        if not confirmed:
            self.status_var.set(self.tr("generation_cancelled"))
            return
        self.status_var.set(self.tr("generating"))

        def operation():
            result = generate_drawings(
                request["excel_path"],
                request["output_path"],
                request["template"],
                formats=request["formats"],
                descending=request["descending"],
            )
            message = self.tr(
                "generated",
                count=len(result.sheets),
                output=request["output_path"],
                logs=log_directory(),
            )
            return {"kind": "generate", "result": result, "message": message}

        self._run_thread(operation)

    def _operation_success(self, result):
        self._set_busy(False)
        if result.get("kind") == "generation_confirmation":
            self._confirm_generation(result)
            return
        self.status_var.set(result.get("message", self.tr("complete")))
        if result.get("kind") == "preview":
            self.preview.set_workbook(result["preview"], self.settings.preview_sheet)
        elif result.get("kind") == "generate":
            messagebox.showinfo(self.tr("success_title"), result["message"])

    def _operation_failed(self, error: Exception):
        self._set_busy(False)
        self.status_var.set(self.tr("failed"))
        message = str(error)
        if self.language == "ja":
            message = "処理を完了できませんでした。入力データ・テンプレート・出力先をご確認ください。\n詳細はログフォルダーの記録をご確認ください。"
        messagebox.showerror(self.tr("error_title"), message)

    def _open_logs(self):
        os.startfile(log_directory())

    def _save_settings(self):
        self.settings = UserSettings(
            excel_path=self.excel_var.get().strip(),
            output_path=self.output_var.get().strip(),
            template_path="",
            active_template_name=self.loaded_template_name,
            frame_dwg_path=self.frame_dwg_var.get().strip(),
            sort_descending=self.sort_var.get() == self.tr("descending"),
            output_pdf=self.pdf_var.get(),
            output_dxf=self.dxf_var.get(),
            output_dwg=self.dwg_var.get(),
            preview_sheet=self.preview.selected_sheet_name(),
            material_symbols=self.material_symbols_var.get(),
        )
        try:
            self.settings.save()
        except OSError as exc:
            logger.warning("配置保存失败：%s", exc)

    def _on_close(self):
        self._save_settings()
        logger.info("软件关闭")
        self.root.destroy()


def run_gui(language: str = "zh", material_symbols: bool | None = None):
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    MaterialDrawingApp(root, language=language, material_symbols=material_symbols)
    root.mainloop()
