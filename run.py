#!/usr/bin/env python3
"""SPX Dashboard - Flask web server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from config import CONFIG

if __name__ == "__main__":
    from app import app

    port = CONFIG.get("spx_dashboard", {}).get("port", 5555)
    host = CONFIG.get("spx_dashboard", {}).get("host", "0.0.0.0")
    debug = CONFIG.get("spx_dashboard", {}).get("debug", False)

    print(f"🚀 SPX Dashboard starting on {host}:{port}")
    app.run(host=host, port=port, debug=debug)
