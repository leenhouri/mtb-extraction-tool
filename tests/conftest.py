"""Make the src/ layout importable when running pytest without an editable install."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
