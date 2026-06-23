"""
SPX 0DTE Scanner Dashboard — Flask Server
Serves /api/scan_results and static index.html.
"""

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys

from flask import Flask, jsonify, render_template, request

# Import config for data source paths
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG

from chart import build_figure

app = Flask(__name__, template_folder="../templates", static_folder="../static")

# Read paths from config
SCANNER_DB_PATH = CONFIG.get("data_sources", {}).get("scanner_db", "../premium_extractor/data/scanner.db")
TV_DB_PATH = CONFIG.get("data_sources", {}).get("tradingview_db", "../tradingView_signal_generator/data/tradingview.db")

# Resolve to absolute paths
DB_PATH = str(Path(SCANNER_DB_PATH).resolve() if Path(SCANNER_DB_PATH).exists() or Path(SCANNER_DB_PATH).is_absolute() else Path(__file__).parent.parent.parent / SCANNER_DB_PATH)
TV_DB = str(Path(TV_DB_PATH).resolve() if Path(TV_DB_PATH).exists() or Path(TV_DB_PATH).is_absolute() else Path(__file__).parent.parent.parent / TV_DB_PATH)

EST_TZ  = "America/New_York"

# ---------------------------------------------------------------------------
# WAL resilience (TASK-2026-235)
# ---------------------------------------------------------------------------

# Max retry attempts on sqlite3.OperationalError when opening tradingview.db
_TV_CONNECT_MAX_ATTEMPTS = 3
# Backoff per attempt (seconds) — 0.2s, 0.4s, 0.6s
_TV_CONNECT_BACKOFFS = (0.2, 0.4, 0.6)
# sqlite connect timeout — short, so retry-loop drives recovery
_TV_CONNECT_TIMEOUT = 2.0


def _connect_tv_with_retry() -> sqlite3.Connection:
    """Open tradingview.db with WAL-aware retry.

    Reader-writer contention on the shared tradingview.db can surface as
    ``sqlite3.OperationalError: unable to open database file`` even though
    the file exists. Retry with short backoff and a 2s connect timeout.

    Returns:
        sqlite3.Connection with WAL journal mode enabled.

    Raises:
        RuntimeError: if all retry attempts fail.
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, _TV_CONNECT_MAX_ATTEMPTS + 1):
        try:
            conn = sqlite3.connect(TV_DB, timeout=_TV_CONNECT_TIMEOUT)
            conn.execute("PRAGMA journal_mode = WAL;")
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
            if attempt < _TV_CONNECT_MAX_ATTEMPTS:
                backoff = _TV_CONNECT_BACKOFFS[attempt - 1]
                time.sleep(backoff)
                continue
            break
    raise RuntimeError(
        f"Failed to open tradingview.db after {_TV_CONNECT_MAX_ATTEMPTS} attempts: {last_err}"
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan_results")
def api_scan_results():
    """Returns scan rows as JSON, filtered by optional from/to date params."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conditions = []
    params     = []

    from_date = request.args.get("from")
    to_date   = request.args.get("to")

    if from_date:
        conditions.append("timestamp_est >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("timestamp_est < ?")
        params.append(to_date + "T23:59:59")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = conn.execute(
        f"SELECT * FROM scan_results {where} ORDER BY id ASC",
        params,
    ).fetchall()
    conn.close()

    return jsonify({
        "rows": [dict(r) for r in rows],
        "count": len(rows),
    })


@app.route("/api/figure")
def api_figure():
    """
    Returns Plotly figure JSON for scan rows.

    Query params:
      from            — ISO date string lower bound (YYYY-MM-DD)
      to              — ISO date string upper bound (YYYY-MM-DD)
      premium_10_op   — operator for 10w premium:  >, <, >=, <=, ==
      premium_10_val  — numeric threshold for 10w premium
      premium_20_op   — operator for 20w premium:  >, <, >=, <=, ==
      premium_20_val  — numeric threshold for 20w premium

    Logic:
      - If op is set AND val parses → filter is ACTIVE → only rows passing
        that leg get a marker drawn for that width
      - If op is empty/None → filter is INACTIVE → all rows get markers
      - SPX spot line always shows full date range (unfiltered)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conditions = []
    params     = []

    from_date = request.args.get("from")
    to_date   = request.args.get("to")

    if from_date:
        conditions.append("timestamp_est >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("timestamp_est < ?")
        params.append(to_date + "T23:59:59")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = conn.execute(
        f"SELECT * FROM scan_results {where} ORDER BY id ASC",
        params,
    ).fetchall()
    conn.close()

    data = [dict(r) for r in rows]

    # Entry filter params — only active when op is set AND val is a valid number
    p10_op  = request.args.get("premium_10_op")  or None
    p10_val = request.args.get("premium_10_val") or None
    p20_op  = request.args.get("premium_20_op")  or None
    p20_val = request.args.get("premium_20_val") or None

    if p10_val is not None:
        try:
            p10_val = float(p10_val)
        except (TypeError, ValueError):
            p10_val = None
    if p20_val is not None:
        try:
            p20_val = float(p20_val)
        except (TypeError, ValueError):
            p20_val = None

    def parse_num(val):
        if val is None: return None
        try: return float(val)
        except (TypeError, ValueError): return None

    rsi_upper = parse_num(request.args.get("rsi_upper"))
    rsi_lower = parse_num(request.args.get("rsi_lower"))

    em_op  = request.args.get("em_op")  or None
    em_val = parse_num(request.args.get("em_val"))
    em_val2= parse_num(request.args.get("em_val2"))

    prox_threshold = None
    if request.args.get("prox_exit") == "1":
        prox_threshold = parse_num(request.args.get("prox_mult")) or 1.0

    # Load fundamentals from TradingView database (must happen before _count_filtered)
    fundamentals = None
    try:
        tv_conn = _connect_tv_with_retry()
        fundamentals = tv_conn.execute("""
            SELECT received_at, price, rsi, macd_hist, adx,
                   bb_upper, bb_middle, bb_lower
            FROM spx_standardized
            WHERE alert_type = 'fundamentals'
            ORDER BY received_at
        """).fetchall()
        tv_conn.close()
    except Exception:
        fundamentals = None  # gracefully skip if DB unavailable

    total_count   = len(data)
    filtered_count = _count_filtered(
        data, p10_op, p10_val, p20_op, p20_val,
        rsi_upper=rsi_upper, rsi_lower=rsi_lower,
        em_op=em_op, em_val=em_val, em_val2=em_val2,
        prox_threshold=prox_threshold,
        fundamentals=fundamentals,
    )

    fig = build_figure(
        rows=data,
        spx_rows=data,
        fundamentals=fundamentals,
        p10_op=p10_op, p10_val=p10_val,
        p20_op=p20_op, p20_val=p20_val,
        rsi_upper=rsi_upper, rsi_lower=rsi_lower,
        em_op=em_op, em_val=em_val, em_val2=em_val2,
        prox_threshold=prox_threshold,
    )

    return jsonify({
        "fig":           fig,
        "filtered_data": data,
        "count":         filtered_count,
        "total_count":   total_count,
    })


def _count_filtered(data, p10_op, p10_val, p20_op, p20_val,
                   rsi_upper=None, rsi_lower=None,
                   em_op=None, em_val=None, em_val2=None,
                   prox_threshold=None,
                   fundamentals=None):
    """Count market-hour rows that have at least one marker drawn after applying all filters."""
    from chart import (_passes_filter, _row_em_multiplier_passes,
                       annotate_breach, annotate_fundamentals, _is_market_hours)

    enriched = annotate_breach(data[:], prox_threshold=prox_threshold) if data else []
    if fundamentals:
        enriched = annotate_fundamentals(enriched, fundamentals)
    enriched = [r for r in enriched if _is_market_hours(r["timestamp_est"])]

    has_10 = (p10_op is not None) and (p10_val is not None)
    has_20 = (p20_op is not None) and (p20_val is not None)
    has_em = em_op is not None and em_val is not None

    has_rsi = rsi_upper is not None and rsi_lower is not None
    if not has_10 and not has_20 and not has_em and not has_rsi:
        return len(enriched)

    count = 0
    for r in enriched:
        # EM filter blocks both sides
        if has_em and not _row_em_multiplier_passes(r, em_op, em_val, em_val2):
            continue

        # RSI gate: RSI > upper → calls only, RSI < lower → puts only, in-band → neither
        if has_rsi:
            rsi = r.get("fundamentals_rsi")
            if rsi is None:
                continue
            rsi_f = float(rsi)
            if rsi_upper >= rsi_f >= rsi_lower:
                continue
            calls_eligible = rsi_f > rsi_upper
            puts_eligible  = rsi_f < rsi_lower
        else:
            calls_eligible = True
            puts_eligible  = True

        # Premium filter — OR across active widths per side, matching build_figure logic
        if not has_10 and not has_20:
            call_passes = True
            put_passes  = True
        elif has_10 and has_20:
            call_passes = (_passes_filter(r.get("call_10_premium"), p10_op, p10_val) or
                           _passes_filter(r.get("call_20_premium"), p20_op, p20_val))
            put_passes  = (_passes_filter(r.get("put_10_premium"),  p10_op, p10_val) or
                           _passes_filter(r.get("put_20_premium"),  p20_op, p20_val))
        elif has_10:
            call_passes = _passes_filter(r.get("call_10_premium"), p10_op, p10_val)
            put_passes  = _passes_filter(r.get("put_10_premium"),  p10_op, p10_val)
        else:
            call_passes = _passes_filter(r.get("call_20_premium"), p20_op, p20_val)
            put_passes  = _passes_filter(r.get("put_20_premium"),  p20_op, p20_val)

        if (calls_eligible and call_passes) or (puts_eligible and put_passes):
            count += 1

    return count


@app.route("/api/dates")
def api_dates():
    """Returns min and max available dates in the DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT MIN(timestamp_est) as min_dt, MAX(timestamp_est) as max_dt FROM scan_results"
    ).fetchone()
    conn.close()
    if row["min_dt"] is None:
        return jsonify({"min_date": None, "max_date": None})
    from dateutil import tz
    min_dt = datetime.fromisoformat(row["min_dt"]).astimezone(tz.gettz(EST_TZ))
    max_dt = datetime.fromisoformat(row["max_dt"]).astimezone(tz.gettz(EST_TZ))
    return jsonify({
        "min_date": min_dt.strftime("%Y-%m-%d"),
        "max_date": max_dt.strftime("%Y-%m-%d"),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=True)