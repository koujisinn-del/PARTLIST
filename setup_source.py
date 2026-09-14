"""Create a project-local environment with the company's approved Python/pip."""
from pathlib import Path
import subprocess
import sys
import venv


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Python 3.12 or newer is required; Python 3.12 64-bit is recommended.")
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    interpreter = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not interpreter.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    # Honour the company's pip configuration/index. No Python/system install,
    # no elevation, no registry edits, and no bundled native converter launch.
    subprocess.run([str(interpreter), "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True)
    subprocess.run([str(interpreter), "-c", "import tkinter, ezdxf, reportlab, matplotlib; print('Dependencies OK')"], check=True)
    print("Ready: .venv\\Scripts\\python.exe main.py --lang zh (or --lang ja)")


if __name__ == "__main__":
    main()
