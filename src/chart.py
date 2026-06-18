"""
SPX 0DTE Scanner Dashboard — Breach Computation + Plotly Figure Builder

Breach logic
────────────
For each row in the filtered result set, look ahead at all future rows in the
same dataset (i.e. with a higher id / later timestamp) that fall on the same
trading day and occur no later than 16:00:00 ET.  Check whether the SPX spot
at any of those future times touched or crossed:

    • call_strike_003   (the short call strike)
    • put_strike_003    (the short put strike)

If yes → BREACHED (red marker).  If no → NO BREACH (green marker).

All timestamps are parsed as America/New_York (EST/EDT).
"""

import math
from datetime import datetime, time as dtime, timedelta

import plotly.graph_objects as go

EST_TZ = "America/New_York"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _na(x):
    """True when x is None or NaN."""
    if x is None:
        return True
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True


def _fmt(v, fmt="{:.2f}"):
    try:
        f = float(v)
        return fmt.format(f) if not math.isnan(f) else "—"
    except (TypeError, ValueError):
        return "—" if v is None else str(v)


def _fmt_strike(v):
    return _fmt(v, "{:.0f}")


def _fmt_delta(v):
    return _fmt(v, "{:.4f}")


def _fmt_mid(v):
    return _fmt(v, "${:.4f}")


def _tz(ts):
    """Parse an ISO timestamp and attach EST/EDT."""
    from dateutil import tz
    try:
        return datetime.fromisoformat(ts).astimezone(tz.gettz(EST_TZ))
    except Exception:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz.gettz(EST_TZ))


def _is_market_hours(ts_str: str) -> bool:
    """
    Return True if the timestamp falls within regular market hours:
    Monday–Friday, 9:30–16:00 ET.
    """
    try:
        dt = _tz(ts_str)
        if dt.weekday() >= 5:          # Sat, Sun
            return False
        market_start = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        market_end   = dt.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_start <= dt <= market_end
    except Exception:
        return False


def _market_day_segments(rows: list[dict]) -> list[list[dict]]:
    """
    Split rows into contiguous market-hour segments.
    Each segment is a list of rows that fall within 9:30–16:00 ET
    on a single weekday.  Gaps (overnight >30 min OR different calendar day)
    break the sequence.  Used so each trading day is its own gap-free trace.
    """
    segments = []
    current  = []

    for r in rows:
        ts_str = r["timestamp_est"]
        if not _is_market_hours(ts_str):
            continue
        if current:
            prev_dt = _tz(current[-1]["timestamp_est"])
            cur_dt  = _tz(ts_str)
            gap_sec = (cur_dt - prev_dt).total_seconds()
            if gap_sec > 30 * 60:          # >30 min gap → new segment
                segments.append(current)
                current = []
        current.append(r)

    if current:
        segments.append(current)
    return segments


def _day_end_timestamps(rows: list[dict]) -> list[str]:
    """
    Return the 16:00 ET timestamp for each unique trading day present in rows.
    Used to draw day-boundary vertical lines.
    """
    from dateutil import tz
    eastern = tz.gettz(EST_TZ)
    days_seen = set()
    ends = []
    for r in rows:
        dt  = _tz(r["timestamp_est"])
        day = dt.date()
        if day not in days_seen:
            days_seen.add(day)
            day_end = datetime.combine(day, dtime(16, 0), tzinfo=eastern)
            ends.append(day_end.strftime("%Y-%m-%dT%H:%M:%S"))
    return ends


# ---------------------------------------------------------------------------
# Breach detection
# ---------------------------------------------------------------------------

def _same_day(a: datetime, b: datetime) -> bool:
    return a.year == b.year and a.month == b.month and a.day == b.day


def _dot_color(l1: bool, l2: bool) -> str:
    if l1:  return "#ff4d4d"   # L1 breach — red
    if l2:  return "#ff9800"   # L2 proximity exit — orange
    return "#3ddc84"           # no exit — green


def annotate_breach(rows: list[dict],
                    prox_threshold: float | None = None) -> list[dict]:
    """
    Mutate every row in place.  Each leg is tracked independently:

      breach_call    — bool  L1: SPX ≥ call_strike at any future tick
      breach_put     — bool  L1: SPX ≤ put_strike  at any future tick
      prox_exit_call — bool  L2: |SPX − call_strike| / EM < prox_threshold
      prox_exit_put  — bool  L2: |SPX − put_strike|  / EM < prox_threshold
      breached       — bool  any L1 triggered

    When prox_threshold is None, L2 detection is skipped.
    Once a leg exits (L1 or L2) no further ticks are checked for that leg.
    """
    sorted_rows = sorted(rows, key=lambda r: r["timestamp_est"])
    n = len(sorted_rows)

    day_cutoffs = {}
    def get_cutoff(dt_obj):
        day = dt_obj.date()
        if day not in day_cutoffs:
            from dateutil import tz
            eastern = tz.gettz(EST_TZ)
            day_cutoffs[day] = datetime.combine(day, dtime(16, 0), tzinfo=eastern)
        return day_cutoffs[day]

    for idx, row in enumerate(sorted_rows):
        ts_cur = _tz(row["timestamp_est"])

        call_k_raw = row.get("call_strike_003")
        put_k_raw  = row.get("put_strike_003")
        em_raw     = row.get("expected_move")

        call_k = float(call_k_raw) if call_k_raw is not None and not _na(call_k_raw) else None
        put_k  = float(put_k_raw)  if put_k_raw  is not None and not _na(put_k_raw)  else None
        em_f   = float(em_raw) if em_raw is not None and not _na(em_raw) and float(em_raw) > 0 else None

        breach_call    = False
        breach_put     = False
        prox_exit_call = False
        prox_exit_put  = False
        call_exited    = call_k is None   # nothing to watch if no strike
        put_exited     = put_k  is None

        cutoff_ts = get_cutoff(ts_cur)

        for j in range(idx + 1, n):
            if call_exited and put_exited:
                break
            future = sorted_rows[j]
            ts_fut = _tz(future["timestamp_est"])
            if not _same_day(ts_cur, ts_fut) or ts_fut > cutoff_ts:
                break
            spx = future.get("spx_spot")
            if spx is None or _na(spx):
                continue
            spx = float(spx)

            if not call_exited:
                if spx >= call_k:                                                    # L1
                    breach_call = True
                    call_exited = True
                elif (prox_threshold is not None and em_f is not None
                      and abs(spx - call_k) / em_f < prox_threshold):               # L2
                    prox_exit_call = True
                    call_exited    = True

            if not put_exited:
                if spx <= put_k:                                                     # L1
                    breach_put = True
                    put_exited = True
                elif (prox_threshold is not None and em_f is not None
                      and abs(spx - put_k) / em_f < prox_threshold):                # L2
                    prox_exit_put = True
                    put_exited    = True

        row["breached"]       = breach_call or breach_put
        row["breach_call"]    = breach_call
        row["breach_put"]     = breach_put
        row["prox_exit_call"] = prox_exit_call
        row["prox_exit_put"]  = prox_exit_put

        spx_spot = row.get("spx_spot")
        row["call_em_multiplier"] = (
            round(abs(spx_spot - call_k) / em_f, 4)
            if em_f is not None and spx_spot is not None and call_k is not None else None
        )
        row["put_em_multiplier"] = (
            round(abs(spx_spot - put_k) / em_f, 4)
            if em_f is not None and spx_spot is not None and put_k  is not None else None
        )

    return rows


# ---------------------------------------------------------------------------
# Fundamentals enrichment
# ---------------------------------------------------------------------------

def annotate_fundamentals(rows: list[dict], fundamentals: list,
                           max_delta_sec: float = 1800.0) -> list[dict]:
    """
    Enrich each scan row with the nearest fundamentals reading from
    TradingView's spx_standardized table (alert_type == 'fundamentals').

    Matching is bidirectional — considers the most recent prior fundamentals
    row AND the next fundamentals row after the scan timestamp, picks whichever
    is closer.  When prior and after are equally distant, prior is preferred.

    A maximum matching window of max_delta_sec (default 1800s = 30 min) applies.
    Rows with no fundamentals within that window get NULL for all indicator fields.
    One scan row → at most one fundamentals row.

    Each enriched row receives: fundamentals_rsi, fundamentals_macd_hist,
    fundamentals_adx, fundamentals_bb_position (calculated), fundamentals_price,
    fundamentals_received_at, fundamentals_delta_sec (matching distance).
    """
    if not fundamentals:
        return [{**r,
                 "fundamentals_rsi": None,
                 "fundamentals_macd_hist": None,
                 "fundamentals_adx": None,
                 "fundamentals_bb_position": None,
                 "fundamentals_price": None,
                 "fundamentals_received_at": None,
                 "fundamentals_delta_sec": None,
                 } for r in rows]

    from bisect import bisect_left, bisect_right
    from datetime import datetime as _dt

    fund_times_str = [f[0] for f in fundamentals]
    fund_times_dt  = [_dt.fromisoformat(ft.replace("Z", "")) for ft in fund_times_str]

    enriched = []
    for r in rows:
        scan_ts_str = r["timestamp_est"]
        scan_ts     = _dt.fromisoformat(scan_ts_str.replace("Z", ""))

        idx_prior = bisect_right(fund_times_str, scan_ts_str) - 1
        idx_after = bisect_left(fund_times_str, scan_ts_str)

        best = None
        best_delta = float("inf")

        if idx_prior >= 0:
            delta = (scan_ts - fund_times_dt[idx_prior]).total_seconds()
            if delta <= max_delta_sec and delta < best_delta:
                best = fundamentals[idx_prior]
                best_delta = delta

        if idx_after < len(fund_times_dt):
            delta = (fund_times_dt[idx_after] - scan_ts).total_seconds()
            if delta <= max_delta_sec and delta < best_delta:
                best = fundamentals[idx_after]
                best_delta = delta

        row = {**r}
        if best:
            row["fundamentals_rsi"]         = best[2]
            row["fundamentals_macd_hist"]   = best[3]
            row["fundamentals_adx"]         = best[4]
            row["fundamentals_price"]       = best[1]
            row["fundamentals_received_at"] = best[0]
            row["fundamentals_delta_sec"]   = round(best_delta, 1)
            bb_upper = best[5]
            bb_lower = best[7]
            bb_range = bb_upper - bb_lower
            spx_spot = r.get("spx_spot")
            row["fundamentals_bb_position"] = (
                round((spx_spot - bb_lower) / bb_range, 4)
                if bb_range and spx_spot is not None else None
            )
        else:
            row["fundamentals_rsi"]         = None
            row["fundamentals_macd_hist"]   = None
            row["fundamentals_adx"]         = None
            row["fundamentals_bb_position"] = None
            row["fundamentals_price"]       = None
            row["fundamentals_received_at"] = None
            row["fundamentals_delta_sec"]   = None
        enriched.append(row)

    return enriched


def _build_hover_text(r: dict) -> str:
    """Build a rich hover line for a single scan row."""
    dt       = _tz(r["timestamp_est"])
    time_str = dt.strftime("%Y-%m-%d %H:%M:%S ET")
    spx      = r.get("spx_spot")

    def _leg_exit(l1_key, l2_key):
        if r.get(l1_key):  return "L1 BREACH"
        if r.get(l2_key):  return "L2 PROX EXIT"
        return "—"

    call_exit = _leg_exit("breach_call", "prox_exit_call")
    put_exit  = _leg_exit("breach_put",  "prox_exit_put")

    return (
        f"<b>SPX {spx:.2f}</b><br>"
        f"Time: {time_str}<br>"
        f"Call Strike: {_fmt_strike(r.get('call_strike_003'))}  |  "
        f"Put Strike: {_fmt_strike(r.get('put_strike_003'))}<br>"
        f"Call Δ: {_fmt_delta(r.get('call_delta'))}  |  "
        f"Put Δ: {_fmt_delta(r.get('put_delta'))}<br>"
        f"Call 10w: {_fmt_mid(r.get('call_10_premium'))}  |  "
        f"Put 10w: {_fmt_mid(r.get('put_10_premium'))}<br>"
        f"Call 20w: {_fmt_mid(r.get('call_20_premium'))}  |  "
        f"Put 20w: {_fmt_mid(r.get('put_20_premium'))}<br>"
        f"EM: ${r.get('expected_move') or 0:.2f}  |  "
        f"ATM Strike: {_fmt_strike(r.get('atm_strike'))}<br>"
        f"<b>Call Exit: {call_exit}  |  Put Exit: {put_exit}</b><br>"
        f"RSI: {_fmt(r.get('fundamentals_rsi'), '{:.1f}')}  |  "
        f"MACD: {_fmt(r.get('fundamentals_macd_hist'), '{:.3f}')}  |  "
        f"ADX: {_fmt(r.get('fundamentals_adx'), '{:.1f}')}<br>"
        f"EMx: {_fmt(r.get('call_em_multiplier'), '{:.1f}x')} / {_fmt(r.get('put_em_multiplier'), '{:.1f}x')}<br>"
        f"<extra></extra>"
    )


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _passes_filter(val, op_str, threshold):
    if val is None:
        return False
    try:
        v = float(val)
        t = float(threshold)
    except (TypeError, ValueError):
        return False
    if math.isnan(v) or math.isnan(t):
        return False
    op_map = {
        ">":  lambda a, b: a > b,
        "<":  lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
    }
    return op_map.get(op_str, lambda a, b: False)(v, t)


def _row_em_multiplier_passes(row, op_str, val, val2=None):
    """Row passes only if BOTH call_em_multiplier AND put_em_multiplier pass."""
    call_em = row.get("call_em_multiplier")
    put_em  = row.get("put_em_multiplier")
    if call_em is None or put_em is None:
        return False
    def passes(v):
        if op_str == "not_between":
            return float(v) < float(val) or float(v) > float(val2)
        return _passes_filter(v, op_str, val)
    return passes(call_em) and passes(put_em)


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------

def build_figure(rows: list[dict],
                 spx_rows: list[dict] | None = None,
                 fundamentals: list | None = None,
                 p10_op: str | None = None, p10_val: float | None = None,
                 p20_op: str | None = None, p20_val: float | None = None,
                 rsi_upper: float | None = None, rsi_lower: float | None = None,
                 em_op: str | None = None, em_val: float | None = None, em_val2: float | None = None,
                 prox_threshold: float | None = None,
                 ) -> dict:
    """
    Build a Plotly figure dict.

    Off-market rows (outside 9:30–16:00 ET Mon–Fri) are excluded from all traces.
    Each trading day is rendered as an independent gap-free segment so there is
    no visible line connecting one day's close to the next day's open.

    Day-boundary vertical dotted lines are drawn at 16:00 ET for each trading
    day present in the dataset.
    """
    if not rows and not spx_rows:
        return {"data": [], "layout": {}}

    annotated = annotate_breach(rows[:], prox_threshold=prox_threshold) if rows else []
    if fundamentals:
        annotated = annotate_fundamentals(annotated, fundamentals)
    spx_source = spx_rows if spx_rows is not None else annotated

    # Filter to market hours only
    market_rows = [r for r in annotated if _is_market_hours(r["timestamp_est"])]
    market_spx  = [r for r in spx_source  if _is_market_hours(r["timestamp_est"])]

    # Split into per-day segments — each segment becomes its own trace
    spx_segments     = _market_day_segments(market_spx)
    marker_segments  = _market_day_segments(market_rows)

    has_10 = (p10_op is not None) and (p10_val is not None)
    has_20 = (p20_op is not None) and (p20_val is not None)
    # show_10w: when 10w filter is active, or when neither is active (base view)
    # show_20w: only when 20w filter is explicitly active
    # Both active (OR): both traces render, each filtered by its own criterion
    show_10w = has_10 or (not has_10 and not has_20)
    show_20w = has_20

    fig = go.Figure()

    # ── SPX spot line — one trace per trading day segment ─────────────────
    for seg in spx_segments:
        fig.add_trace(go.Scatter(
            x=[r["timestamp_est"] for r in seg],
            y=[r.get("spx_spot") for r in seg],
            mode="lines",
            name="SPX Spot",
            line=dict(color="#1e88e5", width=2),
            hovertemplate="SPX: %{y:.2f}<br>%{x}<extra></extra>",
            showlegend=True,
        ))

    # ── Day-boundary vertical lines at 16:00 ET ──────────────────────────
    day_ends = _day_end_timestamps(market_rows)
    for ts in day_ends:
        fig.add_vline(
            x=ts,
            line=dict(color="#3d3d3d", width=1, dash="dot"),
            layer="above",
            opacity=0.6,
        )

    # ── Helper: collect marker x/y/text/color for a set of rows ──────────
    # prem_call_key / prem_put_key: row field for this trace's premium
    # p_op / p_val: premium filter for this trace (None = all pass)
    # rsi_upper/rsi_lower and em_* are captured from outer scope
    def _collect_markers(rows_seg, prem_call_key, prem_put_key, p_op, p_val):
        call_x, call_y, call_texts, call_colors = [], [], [], []
        put_x,  put_y,  put_texts,  put_colors  = [], [], [], []

        for r in rows_seg:
            call_k = r.get("call_strike_003")
            put_k  = r.get("put_strike_003")

            # Premium filter for this trace
            if p_op is not None and p_val is not None:
                call_passes = _passes_filter(r.get(prem_call_key), p_op, p_val)
                put_passes  = _passes_filter(r.get(prem_put_key),  p_op, p_val)
            else:
                call_passes = True
                put_passes  = True

            # EM multiplier filter (blocks both sides if row fails)
            if em_op is not None and em_val is not None:
                if not _row_em_multiplier_passes(r, em_op, em_val, em_val2):
                    call_passes = put_passes = False

            # RSI gate: RSI > upper → calls only, RSI < lower → puts only, in-band/None → neither
            if rsi_upper is not None and rsi_lower is not None:
                rsi = r.get("fundamentals_rsi")
                if rsi is None:
                    call_passes = put_passes = False
                else:
                    rsi_f = float(rsi)
                    if rsi_f > rsi_upper:
                        put_passes = False
                    elif rsi_f < rsi_lower:
                        call_passes = False
                    else:
                        call_passes = put_passes = False

            if call_passes and not _na(call_k):
                call_x.append(r["timestamp_est"])
                call_y.append(call_k)
                call_texts.append(_build_hover_text(r))
                call_colors.append(_dot_color(r.get("breach_call", False),
                                              r.get("prox_exit_call", False)))
            if put_passes and not _na(put_k):
                put_x.append(r["timestamp_est"])
                put_y.append(put_k)
                put_texts.append(_build_hover_text(r))
                put_colors.append(_dot_color(r.get("breach_put", False),
                                             r.get("prox_exit_put", False)))

        return call_x, call_y, call_texts, call_colors, put_x, put_y, put_texts, put_colors

    # ── 10w markers (one trace per day segment) ─────────────────────────
    if show_10w:
        for seg in marker_segments:
            cx, cy, ct, cc, px, py, pt, pc = _collect_markers(
                seg, "call_10_premium", "put_10_premium",
                p10_op if has_10 else None, p10_val if has_10 else None,
            )
            if cx:
                fig.add_trace(go.Scatter(
                    x=cx, y=cy, mode="markers", name="Call 0.03Δ [10w]",
                    marker=dict(color=cc, size=9, line=dict(width=1, color="#1e1e1e")),
                    text=ct, hovertemplate="%{text}", showlegend=True,
                ))
            if px:
                fig.add_trace(go.Scatter(
                    x=px, y=py, mode="markers", name="Put 0.03Δ [10w]",
                    marker=dict(color=pc, size=9, line=dict(width=1, color="#1e1e1e")),
                    text=pt, hovertemplate="%{text}", showlegend=True,
                ))

    # ── 20w markers (one trace per day segment) ─────────────────────────
    if show_20w:
        for seg in marker_segments:
            cx, cy, ct, cc, px, py, pt, pc = _collect_markers(
                seg, "call_20_premium", "put_20_premium",
                p20_op if has_20 else None, p20_val if has_20 else None,
            )
            if cx:
                fig.add_trace(go.Scatter(
                    x=cx, y=cy, mode="markers", name="Call 0.03Δ [20w]",
                    marker=dict(color=cc, size=9, line=dict(width=1, color="#1e1e1e")),
                    text=ct, hovertemplate="%{text}", showlegend=True,
                ))
            if px:
                fig.add_trace(go.Scatter(
                    x=px, y=py, mode="markers", name="Put 0.03Δ [20w]",
                    marker=dict(color=pc, size=9, line=dict(width=1, color="#1e1e1e")),
                    text=pt, hovertemplate="%{text}", showlegend=True,
                ))

    # ── Layout ───────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(text="SPX 0DTE Scanner",
                   font=dict(color="#e6edf3", size=14)),
        margin=dict(l=70, r=40, t=60, b=80),
        legend=dict(orientation="h", yanchor="bottom", y=1.05,
                    xanchor="right", x=1, font=dict(color="#8b949e")),
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font=dict(color="#e6edf3", family="Courier New, monospace"),
        height=550,
        xaxis=dict(type="date",
                   showgrid=True, gridcolor="#21262d",
                   tickfont=dict(color="#8b949e", size=11),
                   ticks="outside", tickcolor="#30363d",
                   showline=True, linecolor="#30363d",
                   rangebreaks=[
                       # Hide before 9:30 AM ET and after 4:00 PM ET on weekdays
                       dict(pattern="hour", bounds=[0, 9.5]),
                       dict(pattern="hour", bounds=[16, 24]),
                       # Skip weekends (Sat–Mon)
                       dict(pattern="day of week", bounds=["sat", "mon"]),
                   ]),
        yaxis=dict(showgrid=True, gridcolor="#21262d",
                   tickfont=dict(color="#8b949e", size=11),
                   ticks="outside", tickcolor="#30363d",
                   showline=True, linecolor="#30363d",
                   title=dict(text="SPX Price", font=dict(color="#8b949e"))),
    )

    return fig.to_dict()