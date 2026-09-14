from __future__ import annotations

from datetime import datetime
import traceback

from material_drawing_generator.runtime_paths import app_data_root


ERROR_LOG = app_data_root() / "启动错误.log"


def show_startup_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "部材表图纸生成器",
            "软件启动失败。\n\n"
            f"错误记录已保存到：\n{ERROR_LOG}\n\n"
            f"错误摘要：{message}",
            parent=root,
        )
        root.destroy()
    except Exception:
        pass


try:
    from main import main

    main(default_language="zh")
except Exception as exc:
    details = (
        f"启动时间：{datetime.now().isoformat(timespec='seconds')}\n\n"
        f"{traceback.format_exc()}"
    )
    ERROR_LOG.write_text(details, encoding="utf-8")
    show_startup_error(str(exc))
