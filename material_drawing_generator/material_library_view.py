"""Read-only two-column material legend using the same symbols as CAD export."""
from __future__ import annotations

import base64
import tkinter as tk
from tkinter import ttk

from .material_preview import symbol_preview_png


class MaterialLibraryView(ttk.Frame):
    ROW_HEIGHT = 36
    HEADER_HEIGHT = 30

    def __init__(self, parent, library, font_filename: str, tr):
        super().__init__(parent)
        self.rules = library.rules
        self.tr = tr
        self.headers = (tr("material_material"), tr("material_symbol"))
        self._images = {}
        for rule in self.rules:
            if not rule.is_unmarked and rule.block_name not in self._images:
                png = symbol_preview_png(rule, font_filename, height_px=28, max_width_px=150)
                self._images[rule.block_name] = tk.PhotoImage(master=self, data=base64.b64encode(png))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.header = tk.Canvas(self, height=self.HEADER_HEIGHT, background="#eef1f5", highlightthickness=0)
        self.header.grid(row=0, column=0, sticky="ew")
        self.body = tk.Canvas(self, background="white", highlightthickness=0, yscrollincrement=self.ROW_HEIGHT)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.body.yview)
        self.scroll.grid(row=1, column=1, sticky="ns")
        self.body.configure(yscrollcommand=self._scrolled)
        self.body.bind("<Configure>", self._draw)
        self.body.bind("<MouseWheel>", self._wheel)
        self.body.bind("<Button-4>", lambda _e: self.body.yview_scroll(-1, "units"))
        self.body.bind("<Button-5>", lambda _e: self.body.yview_scroll(1, "units"))
        self._draw()

    def _scrolled(self, first, last):
        self.scroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self.scroll.grid_remove()
        else:
            self.scroll.grid()

    def _wheel(self, event):
        if event.delta:
            steps = -max(1, abs(int(event.delta / 120))) if event.delta > 0 else max(1, abs(int(event.delta / 120)))
            self.body.yview_scroll(steps, "units")
        return "break"

    def _draw(self, _event=None):
        width = max(self.body.winfo_width(), 2)
        boundary = width * 0.46
        left_center = boundary / 2
        right_center = (boundary + width) / 2
        self.header.delete("all")
        for x, title in zip((left_center, right_center), self.headers):
            self.header.create_text(x, self.HEADER_HEIGHT / 2, text=title, font="TkHeadingFont")
        self.header.create_line(boundary, 0, boundary, self.HEADER_HEIGHT, fill="#cdd3db")
        self.body.delete("all")
        total_height = len(self.rules) * self.ROW_HEIGHT
        for index, rule in enumerate(self.rules):
            y = index * self.ROW_HEIGHT
            center_y = y + self.ROW_HEIGHT / 2
            tags = (f"material_row_{index}",)
            self.body.create_text(left_center, center_y, text=rule.material, font="TkDefaultFont", tags=tags)
            if rule.is_unmarked:
                self.body.create_text(right_center, center_y, text=self.tr("material_unmarked"), font="TkDefaultFont", tags=tags)
            else:
                self.body.create_image(right_center, center_y, image=self._images[rule.block_name], tags=tags)
            self.body.create_line(0, y + self.ROW_HEIGHT, width, y + self.ROW_HEIGHT, fill="#e1e5eb")
        self.body.create_line(boundary, 0, boundary, total_height, fill="#d3d9e1")
        self.body.configure(scrollregion=(0, 0, width, total_height))
