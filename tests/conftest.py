"""
Pytest configuration for spx_dashboard tests.

Sets up the Python path so that:
  `from src.app import ...` resolves to spx_dashboard/src/app.py
  `from chart import ...` resolves to spx_dashboard/src/chart.py
"""
import sys
from pathlib import Path

_root = Path(__file__).parent.parent   # spx_dashboard/ (not tests/)
sys.path.insert(0, str(_root))          # root: makes imports work
sys.path.insert(0, str(_root / "src")) # src/: makes 'from chart import X' work
