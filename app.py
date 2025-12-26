# app.py
import json
import datetime as dt

import pandas as pd
import streamlit as st
import plotly.express as px

from scheduler_core import (
    run_scheduler,
    bom_data as DEFAULT_BOM,
    customer_orders as DEFAULT_ORDERS,
    work_center_capacity as DEFAULT_CAPACITY,
    DEFAULT_RAW_MATERIALS,
)

# --------------------
# PAGE SETUP / STYLE
# --------------------
st.set_page_config(page_title="Mini Manufacturing Scheduler", layout="wide")

st.markdown(
    """
<style>
.block-container { padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1400px; }
h1, h2, h3 { letter-spacing: -0.02em; }
div[data-testid="stMetric"] { border: 1px solid rgba(49, 51, 63, 0.14); padding: 12px; border-radius: 14px; }
div[data-testid="stTabs"] button { font-weight: 700; }
.small-muted { color: rgba(49, 51, 63, 0.65); font-size: 0.95rem; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("Mini Manufacturing Scheduler")
st.markdown('<div class="small-muted">Edit inputs → Run scheduler → Review Gantt + tables.</div>', unsafe_allow_html=True)
st.divider()


# --------------------
# HELPERS
# --------------------
def to_arrow_safe_df(rows):
    """Convert list[dict] or DataFrame to a PyArrow-safe DataFrame for Streamlit."""
    if isinstance(rows, pd.DataFrame):
        df = rows.copy()
    else:
        df = pd.DataFrame(rows or [])

    if df.empty:
        return df

    def fix_val(v):
        if isinstance(v, dt.timedelta):
            return v.total_seconds() / 3600.0
        if isinstance(v, (dt.datetime, dt.date)):
            return v.isoformat()
        if isinstance(v, (dict, list, tuple, set)):
            try:
                return json.dumps(v, default=str)
            except Exception:
                return str(v)
        return v

    for c in df.columns:
        if df[c].dtype == "object":
            df[c] = df[c].map(fix_val)

    return df


def capacity_df_from_obj(cap_obj: dict) -> pd.DataFrame:
    rows = [{"workcenter": k, "capacity": v} for k, v in (cap_obj or {}).items()]
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame([{"workcenter": "", "capacity": 1}])
    return df


def capacity_obj_from_df(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {}
    if "workcenter" not in df.columns or "capacity" not in df.columns:
        return {}

    out = {}
    df2 = df.copy().dropna(subset=["workcenter"])
    for _, r in df2.iterrows():
        wc = str(r.get("workcenter", "")).strip()
        if not wc:
            continue
        try:
            cap = int(r.get("capacity", 1))
        except Exception:
            cap = 1
        out[wc] = cap
    return out


def rm_df_default(bom_df: pd.DataFrame) -> pd.DataFrame:
    """If DEFAULT_RAW_MATERIALS is empty, prefill RM with BOM rows where part_type == RW."""
    try:
        if DEFAULT_RAW_MATERIALS and len(DEFAULT_RAW_MATERIALS) > 0:
            return pd.DataFrame(DEFAULT_RAW_MATERIALS)

        if bom_df is not None and ("part_type" in bom_df.columns) and ("part_name" in bom_df.columns):
            rw = (
                bom_df.loc[bom_df["part_type"].astype(str).str.upper() == "RW", "part_name"]
                .dropna()
                .astype(str)
                .str.strip()
                .tolist()
            )
            rw = [p for p in rw if p]
            if rw:
                return pd.DataFrame([{"part": p} for p in sorted(set(rw))])
    except Exception:
        pass
    return pd.DataFrame(columns=["part"])


def ensure_session_defaults():
    if "orders_df" not in st.session_state:
        st.session_state["orders_df"] = pd.DataFrame(DEFAULT_ORDERS)
    if "bom_df" not in st.session_state:
        st.session_state["bom_df"] = pd.DataFrame(DEFAULT_BOM)
    if "cap_df" not in st.session_state:
        st.session_state["cap_df"] = capacity_df_from_obj(DEFAULT_CAPACITY)
    if "raw_df" not in st.session_state:
        st.session_state["raw_df"] = rm_df_default(st.session_state["bom_df"])

    for k in ["scheduled", "work_orders", "plan", "fig"]:
        if k not in st.session_state:
            st.session_state[k] = None


def reset_to_defaults():
    st.session_state["orders_df"] = pd.DataFrame(DEFAULT_ORDERS)
    st.session_state["bom_df"] = pd.DataFrame(DEFAULT_BOM)
    st.session_state["cap_df"] = capacity_df_from_obj(DEFAULT_CAPACITY)
    st.session_state["raw_df"] = rm_df_default(st.session_state["bom_df"])
    st.session_state["scheduled"] = None
    st.session_state["work_orders"] = None
    st.session_state["plan"] = None
    st.session_state["fig"] = None


def auto_detect_numeric_time_cols(df: pd.DataFrame):
    """
    Try to infer start/end numeric time columns.
    - Prefer columns whose names include: start/end, in/out, input/output, begin/finish.
    - Otherwise, try to find a pair of numeric columns where (end >= start) for most rows.
    Returns (start_col, end_col) or (None, None).
    """
    if df is None or df.empty:
        return None, None

    cols = list(df.columns)
    lower = [c.lower() for c in cols]

    # Name-based candidates
    start_name_cands = ["start", "begin", "in", "input", "from"]
    end_name_cands = ["end", "finish", "out", "output", "to"]

    def pick_name(cands):
        for cand in cands:
            for i, c in enumerate(lower):
                if c == cand or cand in c:
                    return cols[i]
        return None

    start_col = pick_name(start_name_cands)
    end_col = pick_name(end_name_cands)

    # If both found and numeric-ish -> done
    if start_col and end_col:
        s = pd.to_numeric(df[start_col], errors="coerce")
        e = pd.to_numeric(df[end_col], errors="coerce")
        if (s.notna().sum() > 0) and (e.notna().sum() > 0):
            return start_col, end_col

    # Otherwise brute force numeric pair search
    numeric_cols = []
    for c in cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().mean() > 0.8:  # mostly numeric
            numeric_cols.append(c)

    best = (None, None, -1.0)
    for i in range(len(numeric_cols)):
        for j in range(len(numeric_cols)):
            if i == j:
                continue
            a = numeric_cols[i]
            b = numeric_cols[j]
            s = pd.to_numeric(df[a], errors="coerce")
            e = pd.to_numeric(df[b], errors="coerce")
            mask = s.notna() & e.notna()
            if mask.sum() < 5:
                continue
            score = (e[mask] >= s[mask]).mean()
            if score > best[2]:
                best = (a, b, score)

    if best[2] >= 0.95:  # very likely start/end
        return best[0], best[1]

    return None, None


def auto_detect_resource_col(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    cols = list(df.columns)
    lower = [c.lower() for c in cols]
    for cand in ["workcenter", "work_center", "resource", "wc", "machine", "tool"]:
        for i, c in enumerate(lower):
            if c == cand or cand in c:
                return cols[i]
    return None


def to_datetime_from_numeric(series: pd.Series, base_date: dt.datetime, unit: str) -> pd.Series:
    """
    Convert numeric offset -> datetime using base_date and a chosen unit.
    unit: seconds | minutes | hours | days
    """
    s = pd.to_numeric(series, errors="coerce")
    if unit == "seconds":
        return base_date + pd.to_timedelta(s, unit="s")
    if unit == "minutes":
        return base_date + pd.to_timedelta(s, unit="m")
    if unit == "hours":
        return base_date + pd.to_timedelta(s, unit="h")
    if unit == "days":
        return base_date + pd.to_timedelta(s, unit="D")
    # default minutes
    return base_date + pd.to_timedelta(s, unit="m")


def build_numeric_gantt(df_in: pd.DataFrame, start_col: str, end_col: str, y_col: str,
                        base_date: dt.datetime, unit: str, label_col: str | None):
    df = df_in.copy()

    # ensure numeric
    s_num = pd.to_numeric(df[start_col], errors="coerce")
    e_num = pd.to_numeric(df[end_col], errors="coerce")
    df = df.loc[s_num.notna() & e_num.notna()].copy()

    if df.empty:
        raise ValueError("No rows had numeric start/end after coercion.")

    df["_start_dt"] = to_datetime_from_numeric(df[start_col], base_date, unit)
    df["_end_dt"] = to_datetime_from_numeric(df[end_col], base_date, unit)

    df = df.dropna(subset=["_start_dt", "_end_dt", y_col])
    if df.empty:
        raise ValueError("After building datetimes, no rows remained.")

    if not label_col or label_col not in df.columns:
        label_col = y_col

    fig = px.timeline(
        df,
        x_start="_start_dt",
        x_end="_end_dt",
        y=y_col,
        hover_data=[label_col] if label_col in df.columns else None,
    )
    fig.update_yaxes(autorange="reversed")
    return fig


ensure_session_defaults()

# --------------------
# SIDEBAR
# --------------------
with st.sidebar:
    st.header("Controls")
    show_chart = st.toggle("Show Gantt chart", value=True)
    run = st.button("Run scheduler", type="primary", use_container_width=True)
    st.button("Reset inputs", on_click=reset_to_defaults, use_container_width=True)


# --------------------
# FLATTENED TABS
# --------------------
tab_orders, tab_bom, tab_cap, tab_rm, tab_results = st.tabs(
    ["🧾 Orders", "🧩 BOM / Routing", "🏭 Capacity", "🧱 Raw Materials", "📈 Results"]
)

with tab_orders:
    st.subheader("Customer orders")
    st.session_state["orders_df"] = st.data_editor(
        to_arrow_safe_df(st.session_state["orders_df"]),
        use_container_width=True,
        num_rows="dynamic",
        key="orders_editor",
    )

with tab_bom:
    st.subheader("BOM / routing data")
    st.session_state["bom_df"] = st.data_editor(
        to_arrow_safe_df(st.session_state["bom_df"]),
        use_container_width=True,
        num_rows="dynamic",
        height=560,
        key="bom_editor",
    )

with tab_cap:
    st.subheader("Work-center capacity")
    st.session_state["cap_df"] = st.data_editor(
        to_arrow_safe_df(st.session_state["cap_df"]),
        use_container_width=True,
        num_rows="dynamic",
        height=420,
        key="cap_editor",
    )

with tab_rm:
    st.subheader("Raw materials")
    st.session_state["raw_df"] = st.data_editor(
        to_arrow_safe_df(st.session_state["raw_df"]),
        use_container_width=True,
        num_rows="dynamic",
        height=380,
        key="raw_editor",
    )


# --------------------
# RUN SCHEDULER
# --------------------
if run:
    try:
        orders = st.session_state["orders_df"].to_dict(orient="records")
        bom = st.session_state["bom_df"].to_dict(orient="records")
        capacity = capacity_obj_from_df(st.session_state["cap_df"])
        raw_materials = st.session_state["raw_df"].to_dict(orient="records")

        with st.spinner("Scheduling..."):
            scheduled, work_orders, plan, fig = run_scheduler(
                bom,
                orders,
                capacity,
                raw_materials,
                show_chart=show_chart,
            )

        st.session_state["scheduled"] = scheduled
        st.session_state["work_orders"] = work_orders
        st.session_state["plan"] = plan
        st.session_state["fig"] = fig

        st.success(f"Done. Scheduled runs: {len(scheduled)} | Work orders: {len(work_orders)}")

    except Exception as e:
        st.error(str(e))


# --------------------
# RESULTS
# --------------------
with tab_results:
    scheduled = st.session_state.get("scheduled")
    work_orders = st.session_state.get("work_orders")
    plan = st.session_state.get("plan")
    fig = st.session_state.get("fig")

    if not scheduled:
        st.info("Run the scheduler from the sidebar. Results will show here.")
    else:
        df_sched = to_arrow_safe_df(scheduled)

        m1, m2, m3 = st.columns(3)
        m1.metric("Scheduled rows", len(scheduled))
        m2.metric("Work orders", len(work_orders) if work_orders else 0)
        m3.metric("Columns", len(df_sched.columns))

        st.divider()

        # --------------------
        # GANTT (core fig OR numeric fallback)
        # --------------------
        if show_chart:
            if fig is not None:
                st.subheader("Gantt chart")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("scheduler_core returned fig=None. Building Gantt from numeric time columns…")

                # Auto detect numeric start/end and resource
                auto_start, auto_end = auto_detect_numeric_time_cols(df_sched)
                auto_y = auto_detect_resource_col(df_sched)

                with st.expander("Gantt Builder (auto + manual override)", expanded=True):
                    st.write("Detected (auto):", {"start": auto_start, "end": auto_end, "resource": auto_y})

                    cols = list(df_sched.columns)
                    cols_none = ["(none)"] + cols

                    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 1.2])
                    with c1:
                        start_col = st.selectbox("Start (numeric) column", cols_none, index=(cols_none.index(auto_start) if auto_start in cols_none else 0))
                    with c2:
                        end_col = st.selectbox("End (numeric) column", cols_none, index=(cols_none.index(auto_end) if auto_end in cols_none else 0))
                    with c3:
                        y_col = st.selectbox("Resource / Workcenter column", cols_none, index=(cols_none.index(auto_y) if auto_y in cols_none else 0))
                    with c4:
                        label_col = st.selectbox("Hover label (optional)", cols_none, index=0)

                    st.caption("Your example 0→100, 100→190 means these are the two columns to pick for Start/End.")

                    d1, d2 = st.columns([1, 1])
                    with d1:
                        unit = st.selectbox("Time unit for numeric columns", ["minutes", "seconds", "hours", "days"], index=0)
                    with d2:
                        base = st.date_input("Base date for chart", value=dt.date.today())
                        base_dt = dt.datetime.combine(base, dt.time(0, 0, 0))

                    show_preview = st.checkbox("Show schedule preview + columns", value=False)
                    if show_preview:
                        st.write("Columns:", cols)
                        st.dataframe(df_sched.head(30), use_container_width=True)

                    build = st.button("Build Gantt", type="primary")
                    if build:
                        try:
                            if start_col == "(none)" or end_col == "(none)" or y_col == "(none)":
                                raise ValueError("Pick Start, End, and Resource columns.")
                            hover = None if label_col == "(none)" else label_col

                            gantt = build_numeric_gantt(
                                df_sched,
                                start_col=start_col,
                                end_col=end_col,
                                y_col=y_col,
                                base_date=base_dt,
                                unit=unit,
                                label_col=hover,
                            )
                            st.subheader("Gantt chart (numeric fallback)")
                            st.plotly_chart(gantt, use_container_width=True)
                        except Exception as e:
                            st.error(f"Gantt build failed: {e}")

        st.subheader("Scheduled table")
        st.dataframe(df_sched, use_container_width=True, height=520)

        with st.expander("Work orders"):
            st.dataframe(to_arrow_safe_df(work_orders), use_container_width=True, height=360)

        with st.expander("Plan ledger"):
            if plan and isinstance(plan, dict):
                st.dataframe(to_arrow_safe_df(plan.get("ledger", [])), use_container_width=True, height=360)
            else:
                st.info("No plan ledger available.")
