from __future__ import annotations

from datetime import datetime
from pathlib import Path
import traceback

from material_drawing_generator.runtime_paths import app_data_root


ERROR_LOG = app_data_root() / "起動エラー.log"


def show_startup_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "部材表図面生成ツール",
            "ソフトウェアを起動できませんでした。\n\n"
            f"エラーログ：\n{ERROR_LOG}\n\n"
            f"概要：{message}",
            parent=root,
        )
        root.destroy()
    except Exception:
        pass


try:
    from main import main

    main(default_language="ja")
except Exception as exc:
    details = (
        f"起動日時：{datetime.now().isoformat(timespec='seconds')}\n\n"
        f"{traceback.format_exc()}"
    )
    ERROR_LOG.write_text(details, encoding="utf-8")
    show_startup_error(str(exc))
