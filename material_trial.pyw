"""Compatibility shortcut: opens the unified app with material symbols enabled."""
import sys
from pathlib import Path
import runpy

if "--material-symbols" not in sys.argv:
    sys.argv.append("--material-symbols")
runpy.run_path(str(Path(__file__).with_name("app_launcher.pyw")), run_name="__main__")
