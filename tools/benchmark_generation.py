"""Benchmark the unmodified generation service, never the user's original XLSX.

Workers run sequentially in fresh Python processes with a shared, primed disk
cache. Timings start at generate_drawings(), not interpreter startup. Per-stage
wrappers only collect elapsed time; all real rendering/conversion calls run.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "tmp" / "benchmark_20260904"
sys.path.insert(0, str(ROOT))
os.environ["LOCALAPPDATA"] = str(BENCH / "appdata")
os.environ["PYTHONIOENCODING"] = "utf-8"
SOURCE = ROOT / "テスト用部材データ0904.xlsx"
FRAME = ROOT / "図面テンプレート.dwg"
EXPANDED = ROOT / "outputs" / "01a05d12-c37e-7963-95ee-47146f559f0a" / "benchmark_20260904" / "测试用_2F至13F.xlsx"
FORMAT_MODES = {"pdf": {"pdf"}, "dwg": {"dwg"}, "pdf_dwg": {"pdf", "dwg"}}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def worker(size: int, mode: str, repetition: int) -> None:
    from material_drawing_generator import service
    from material_drawing_generator.template import SiteTemplate, default_template_path

    stages = defaultdict(float)
    calls = defaultdict(int)
    for function_name in ("read_workbook", "apply_submission_highlighting", "render_dxf", "render_cad_pdf", "convert_dxf_to_dwg"):
        original = getattr(service, function_name)

        def timed(*args, _name=function_name, _original=original, **kwargs):
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                stages[_name] += time.perf_counter() - started
                calls[_name] += 1

        setattr(service, function_name, timed)
    source = BENCH / "baseline_2_sheets.xlsx" if size == 2 else EXPANDED
    before = digest(source)
    template = SiteTemplate.load(default_template_path()).with_frame_dwg(FRAME)
    run_id = f"{size}_{mode}_r{repetition}"
    output = BENCH / "runs" / run_id
    started = time.perf_counter()
    result = service.generate_drawings(source, output, template, FORMAT_MODES[mode])
    seconds = time.perf_counter() - started
    assert len(result.sheets) == size
    assert sum(item.output_rows for item in result.sheets) == size // 2 * 380
    files = []
    for sheet in result.sheets:
        assert sheet.input_rows == sheet.output_rows
        for fmt in FORMAT_MODES[mode]:
            path = Path(sheet.files[fmt])
            assert path.stat().st_size > 1000, path
            with path.open("rb") as stream:
                signature = stream.read(8)
            assert signature.startswith(b"%PDF" if fmt == "pdf" else b"AC10"), path
            files.append({"format": fmt, "path": str(path), "bytes": path.stat().st_size})
    assert digest(source) == before, "Benchmark input was unexpectedly modified"
    payload = {"run_id": run_id, "size": size, "mode": mode, "repetition": repetition,
               "seconds": seconds, "stages": dict(stages), "calls": dict(calls),
               "rows": sum(item.output_rows for item in result.sheets), "files": files,
               "validated": True}
    write_json(BENCH / f"{run_id}.json", payload)
    print(f"DONE {run_id}: {seconds:.3f} seconds", flush=True)


def run() -> None:
    from material_drawing_generator.template import SiteTemplate, default_template_path
    from material_drawing_generator.xlsx_reader import read_workbook, read_workbook_preview

    BENCH.mkdir(parents=True, exist_ok=True)
    protected = {str(path): digest(path) for path in (SOURCE, FRAME, ROOT / "図面テンプレート_テキスト枠.dwg")}
    template = SiteTemplate.load(default_template_path())
    original = read_workbook(SOURCE, template)
    expanded = read_workbook(EXPANDED, template)
    assert len(original.sheets) == 2 and len(expanded.sheets) == 12
    for index, sheet in enumerate(expanded.sheets):
        reference = original.sheets[index % 2]
        assert [asdict(row) for row in sheet.source_rows] == [asdict(row) for row in reference.source_rows]
        record = expanded.drawing_list.record_for_sheet(sheet.name)
        expected = original.drawing_list.record_for_sheet(reference.name)
        assert record is not None and record.revision_dates == expected.revision_dates
    preview = read_workbook_preview(EXPANDED, template)
    assert all((1, 2, 1, 3) in sheet.merged_ranges for sheet in preview.sheets[1:])
    shutil.copy2(SOURCE, BENCH / "baseline_2_sheets.xlsx")

    def dispatch(size: int, mode: str, repetition: int):
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(size), mode, str(repetition)]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
        if completed.returncode:
            raise RuntimeError(completed.stdout + "\n" + completed.stderr)
        print(completed.stdout.strip(), flush=True)
        return json.loads((BENCH / f"{size}_{mode}_r{repetition}.json").read_text(encoding="utf-8"))

    print("Priming frame/font disk caches (excluded from reported timings)...", flush=True)
    warmup = dispatch(2, "pdf_dwg", 0)
    results = []
    modes = list(FORMAT_MODES)
    for repetition in range(1, 4):
        # Rotate order to reduce systematic bias from file-system/thermal state.
        order = modes[repetition - 1:] + modes[:repetition - 1]
        for size in (2, 12):
            for mode in order:
                results.append(dispatch(size, mode, repetition))
                write_json(BENCH / "timings_partial.json", results)
    summary = []
    for size in (2, 12):
        for mode in modes:
            group = [item for item in results if item["size"] == size and item["mode"] == mode]
            times = [item["seconds"] for item in group]
            summary.append({"sheets": size, "mode": mode, "median_seconds": statistics.median(times),
                            "min_seconds": min(times), "max_seconds": max(times), "runs": times,
                            "stage_medians": {key: statistics.median(item["stages"].get(key, 0) for item in group)
                                              for key in group[0]["stages"]}})
    assert all(digest(Path(filename)) == expected for filename, expected in protected.items())
    report = {"measured_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "python": sys.version,
              "platform": platform.platform(), "processor": os.environ.get("PROCESSOR_IDENTIFIER", ""),
              "logical_processors": os.cpu_count(), "source": str(SOURCE), "frame": str(FRAME),
              "source_hashes": protected, "test_input": str(EXPANDED), "input_sizes": {"2": 380, "12": 2280},
              "measurement": "Sequential fresh processes, disk caches primed; generate_drawings entry to return; original code unmodified",
              "excluded": ["GUI clicks/preview", "printer output", "drawing-list PDF", "PDF merging", "workbook duplication"],
              "warmup": warmup, "summary": summary, "runs": results}
    write_json(BENCH / "timings.json", report)
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=3)
    args = parser.parse_args()
    if args.worker:
        worker(int(args.worker[0]), args.worker[1], int(args.worker[2]))
    else:
        run()
