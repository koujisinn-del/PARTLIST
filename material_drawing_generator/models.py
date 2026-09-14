from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


def normalized_text(value: Any) -> str:
    if value is None:
        return ""
    # Preserve the engineering enclosed-B mark before NFKC folds it into a
    # plain ``B``.  A bracketed ASCII fallback is readable in every CAD font.
    source = str(value).replace("\U0001f131", "(B)").replace("\U0001f171", "(B)")
    text = unicodedata.normalize("NFKC", source).strip()
    return re.sub(r"\s+", " ", text)


def source_text(value: Any) -> str:
    """Display data without compatibility-folding engineering symbols."""
    return "" if value is None else str(value)


def natural_sort_key(value: str):
    text = normalized_text(value).casefold()
    parts = re.split(r"(\d+(?:\.\d+)?)", text)
    key: list[tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        try:
            key.append((0, float(part)))
        except ValueError:
            key.append((1, part))
    return key


@dataclass(slots=True)
class SectionRow:
    source_row: int
    member_name: str
    category: str
    position: str = ""
    section: str = ""
    material: str = ""
    joint: str = ""
    remarks: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def value_for(self, field_name: str) -> str:
        if hasattr(self, field_name):
            return source_text(getattr(self, field_name))
        return source_text(self.extra.get(field_name, ""))


@dataclass(slots=True)
class MemberGroup:
    category: str
    member_name: str
    rows: list[SectionRow]
    first_source_row: int


@dataclass(slots=True)
class SheetData:
    name: str
    header_row: int
    source_rows: list[SectionRow]
    groups: list[MemberGroup]
    warnings: list[str] = field(default_factory=list)

    @property
    def source_row_count(self) -> int:
        return len(self.source_rows)

    @property
    def output_subrow_count(self) -> int:
        return sum(len(group.rows) for group in self.groups)

    def groups_for_category(self, category: str) -> list[MemberGroup]:
        wanted = normalized_text(category).casefold()
        return [
            group
            for group in self.groups
            if normalized_text(group.category).casefold() == wanted
        ]

    @property
    def categories(self) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for group in self.groups:
            key = normalized_text(group.category).casefold()
            if key and key not in seen:
                seen.add(key)
                result.append(group.category)
        return result


@dataclass(slots=True)
class DrawingRecord:
    source_row: int
    drawing_name: str
    drawing_number: str
    version_dates: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def latest_version(self) -> str:
        return self.version_dates[-1][0] if self.version_dates else ""

    @property
    def latest_date(self) -> str:
        return self.version_dates[-1][1] if self.version_dates else ""

    @property
    def latest_column(self) -> int | None:
        return self.version_dates[-1][2] if self.version_dates else None

    @property
    def revision_dates(self) -> list[str]:
        return [date_value for _, date_value, _ in self.version_dates]


@dataclass(slots=True)
class DrawingListData:
    sheet_name: str
    worksheet_path: str
    header_row: int
    name_column: int
    number_column: int
    version_columns: list[tuple[str, int]]
    records: list[DrawingRecord]
    submission_date_cell: str = ""
    submission_date: str = ""

    def record_for_sheet(self, sheet_name: str) -> DrawingRecord | None:
        wanted = normalized_text(sheet_name).casefold()
        for record in self.records:
            if normalized_text(record.drawing_name).casefold() == wanted:
                return record
        return None

    def highlighted_cells(self) -> set[tuple[int, int]]:
        if not self.submission_date:
            return set()
        wanted = normalized_text(self.submission_date).casefold()
        cells: set[tuple[int, int]] = set()
        for record in self.records:
            if normalized_text(record.latest_date).casefold() != wanted:
                continue
            cells.add((record.source_row, self.name_column))
            cells.add((record.source_row, self.number_column))
            if record.latest_column is not None:
                cells.add((record.source_row, record.latest_column))
        return cells


@dataclass(slots=True)
class PreviewCellStyle:
    horizontal: str = ""
    vertical: str = ""
    wrap_text: bool = False
    font_name: str = ""
    font_size: float = 9.0
    bold: bool = False
    italic: bool = False


@dataclass(slots=True)
class PreviewSheet:
    name: str
    rows: list[tuple[int, dict[int, Any]]]
    highlighted_cells: set[tuple[int, int]] = field(default_factory=set)
    merged_ranges: list[tuple[int, int, int, int]] = field(default_factory=list)
    row_heights: dict[int, float] = field(default_factory=dict)
    default_row_height: float = 15.0
    cell_styles: dict[tuple[int, int], PreviewCellStyle] = field(default_factory=dict)

    @property
    def max_column(self) -> int:
        value_column = max((max(values, default=-1) for _, values in self.rows), default=-1)
        merged_column = max((end_column for _, _, _, end_column in self.merged_ranges), default=-1)
        return max(value_column, merged_column) + 1


@dataclass(slots=True)
class WorkbookPreview:
    source_path: str
    sheets: list[PreviewSheet]
    drawing_list: DrawingListData | None = None


@dataclass(slots=True)
class WorkbookData:
    source_path: str
    sheets: list[SheetData]
    skipped_sheets: list[str] = field(default_factory=list)
    drawing_list: DrawingListData | None = None


def group_section_rows(
    rows: list[SectionRow],
    category_order: list[str],
    descending: bool = False,
) -> list[MemberGroup]:
    grouped: dict[tuple[str, str], MemberGroup] = {}
    for row in rows:
        key = (
            normalized_text(row.category).casefold(),
            normalized_text(row.member_name),
        )
        if key not in grouped:
            grouped[key] = MemberGroup(
                category=row.category,
                member_name=row.member_name,
                rows=[],
                first_source_row=row.source_row,
            )
        grouped[key].rows.append(row)

    order_map = {
        normalized_text(category).casefold(): index
        for index, category in enumerate(category_order)
    }

    def group_key(group: MemberGroup):
        category_key = normalized_text(group.category).casefold()
        return (
            order_map.get(category_key, len(order_map)),
            natural_sort_key(group.member_name),
            group.first_source_row,
        )

    ordered: list[MemberGroup] = []
    categories: dict[str, list[MemberGroup]] = {}
    for group in grouped.values():
        categories.setdefault(normalized_text(group.category).casefold(), []).append(group)

    encounter_order = {key: index for index, key in enumerate(categories)}
    category_keys = sorted(
        categories,
        key=lambda key: (
            0 if key in order_map else 1,
            order_map.get(key, encounter_order[key]),
        ),
    )
    for category_key in category_keys:
        category_groups = categories[category_key]
        category_groups.sort(key=group_key, reverse=descending)
        ordered.extend(category_groups)
    return ordered
