"""Check, prepare and start the project-local Python 3.12 environment."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
ENVIRONMENT = ROOT / ".venv"
INTERPRETER = ENVIRONMENT / "Scripts" / "python.exe"
WINDOWED_INTERPRETER = ENVIRONMENT / "Scripts" / "pythonw.exe"
HEALTH_CHECK = (
    "import sys, tkinter, ezdxf, reportlab, matplotlib; "
    "from main import main; "
    "assert sys.version_info[:2] == (3, 12), 'Python 3.12 required'; "
    "assert sys.maxsize > 2**32, '64-bit Python required'"
)
VERSION_CHECK = (
    "import sys; "
    "assert sys.version_info[:2] == (3, 12), 'Python 3.12 required'; "
    "assert sys.maxsize > 2**32, '64-bit Python required'"
)


def _run_check(code: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(INTERPRETER), "-c", code], cwd=ROOT,
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def environment_health() -> tuple[bool, str]:
    if not INTERPRETER.is_file():
        return False, "项目环境中没有 Python。"
    if not WINDOWED_INTERPRETER.is_file():
        return False, "项目环境中没有 pythonw.exe。"
    ready, reason = _run_check(HEALTH_CHECK)
    return ready, reason or ("环境可用。" if ready else "环境检查失败。")


def setup_environment() -> None:
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise SystemExit("请使用公司批准的 64 位 Python 3.12；不要用 Python 3.14 建立环境。")
    if ENVIRONMENT.exists():
        if not INTERPRETER.is_file() or not WINDOWED_INTERPRETER.is_file():
            raise SystemExit("已有 .venv 目录但环境不完整。请先将它重命名为 .venv_old，再双击启动程序。")
        version_ok, reason = _run_check(VERSION_CHECK)
        if not version_ok:
            raise SystemExit(
                "已有 .venv 不是可用的 64 位 Python 3.12 环境，或原安装路径已失效。"
                "请先将它重命名为 .venv_old，再双击启动程序。\n" + reason
            )
    else:
        print("首次启动：正在建立本项目的 Python 3.12 环境…", flush=True)
        venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
    print("正在按公司已配置的软件源检查和安装组件…", flush=True)
    subprocess.run(
        [str(INTERPRETER), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
        cwd=ROOT, check=True,
    )
    ready, reason = environment_health()
    if not ready:
        raise SystemExit("组件安装后环境仍未通过检查：\n" + reason)
    print("Dependencies OK / 环境检查通过。", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", choices=("zh", "ja"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise SystemExit("请使用公司批准的 64 位 Python 3.12；不要用 Python 3.14。")
    ready, reason = environment_health()
    if args.check:
        print("Dependencies OK / 环境检查通过。" if ready else "环境未通过检查：" + reason)
        from material_drawing_generator.runtime_paths import dwg_tools_available
        print("DWG 转换组件：" + ("可用" if dwg_tools_available() else "未找到；PDF/DXF 仍可使用"))
        if not ready:
            raise SystemExit(1)
        return
    if not ready:
        print("环境需要准备或补齐组件：" + reason, flush=True)
        try:
            setup_environment()
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"组件安装失败（退出码 {exc.returncode}）。请拍下上方报错。") from exc
    if args.launch:
        launcher = ROOT / ("app_launcher.pyw" if args.launch == "zh" else "launcher_ja.pyw")
        subprocess.Popen([str(WINDOWED_INTERPRETER), str(launcher)], cwd=ROOT)
        print("已发送启动请求；若窗口未出现，请查看启动错误日志。", flush=True)


if __name__ == "__main__":
    main()
