#!/usr/bin/env python3
"""SPX Dashboard - Flask web server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Placeholder: Full dashboard app.py from monolith to be integrated
if __name__ == "__main__":
    print("🚀 SPX Dashboard")
    print("Note: Full app.py and chart.py from monolith needs to be ported here")
    print("This should serve the dashboard on port 5555 by default")
    print("Reading from ../premium_extractor/data/scanner.db")
    print("Reading from ../tradingView_signal_generator/data/tradingview.db")
