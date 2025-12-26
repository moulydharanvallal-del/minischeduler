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


def convert_numeric_to_datetime(s: pd.Series, base_dt: dt.datetime, unit: str) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    unit_map = {"seconds": "s", "minutes": "m", "hours": "h", "days": "D"}
    return base_dt + pd.to_timedelta(x, unit=unit_map.get(unit, "m"))


def build_gantt(df_in: pd.DataFrame, start_col: str, end_col: str, y_col: str,
                time_mode: str, unit: str, base_date: dt.date, label_col: str | None):
    """
    time_mode:
      - "datetime": start/end are datetime parseable
      - "numeric": start/end are numeric offsets (e.g., minutes from 0)
    """
    df = df_in.copy()

    if time_mode == "datetime":
        df["_start"] = pd.to_datetime(df[start_col], errors="coerce")
        df["_end"] = pd.to_datetime(df[end_col], errors="coerce")
    else:
        base_dt = dt.datetime.combine(base_date, dt.time(0, 0, 0))
        df["_start"] = convert_numeric_to_datetime(df[start_col], base_dt, unit)
        df["_end"] = convert_numeric_to_datetime(df[end_col], base_dt, unit)

    df = df.dropna(subset=["_start", "_end", y_col])
    if df.empty:
        raise ValueError("No rows left after parsing start/end/resource. Check your selected columns.")

    if label_col is None or label_col not in df.columns:
        label_col = y_col

    fig = px.timeline(
        df,
        x_start="_start",
        x_end="_end",
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
# RUN
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

        # Always show columns + preview so we stop guessing forever
        with st.expander("Schedule output (columns + preview)", expanded=True):
            st.write("Columns:", list(df_sched.columns))
            st.dataframe(df_sched.head(40), use_container_width=True)

        # Gantt: show core if available
        if show_chart and fig is not None:
            st.subheader("Gantt chart (from scheduler_core)")
            st.plotly_chart(fig, use_container_width=True)

        # Gantt Builder (always available)
        st.markdown("## Gantt Builder")
        st.caption("Pick the columns that represent Start/End time and Resource. Works with numeric time buckets like 0→100.")

        cols = list(df_sched.columns)
        cols_none = ["(none)"] + cols

        # Heuristic default picks (very mild)
        low = [c.lower() for c in cols]
        def guess_one(keys):
            for k in keys:
                for i, c in enumerate(low):
                    if c == k or k in c:
                        return cols[i]
            return None

        default_start = guess_one(["start", "in", "input", "begin", "from"])
        default_end = guess_one(["end", "out", "output", "finish", "to"])
        default_y = guess_one(["workcenter", "work_center", "resource", "wc", "tool", "machine"])

        c1, c2, c3 = st.columns(3)
        with c1:
            start_col = st.selectbox("Start column", cols_none, index=(cols_none.index(default_start) if default_start in cols_none else 0))
        with c2:
            end_col = st.selectbox("End column", cols_none, index=(cols_none.index(default_end) if default_end in cols_none else 0))
        with c3:
            y_col = st.selectbox("Resource / Workcenter column", cols_none, index=(cols_none.index(default_y) if default_y in cols_none else 0))

        time_mode = st.radio("Time mode", ["numeric", "datetime"], horizontal=True, index=0)
        unit = st.selectbox("Numeric unit", ["minutes", "seconds", "hours", "days"], index=0)
        base_date = st.date_input("Base date (numeric mode)", value=dt.date.today())
        label_col = st.selectbox("Hover label (optional)", cols_none, index=0)

        build = st.button("Build Gantt", type="primary")

        if build:
            try:
                if start_col == "(none)" or end_col == "(none)" or y_col == "(none)":
                    raise ValueError("Select Start, End, and Resource columns.")
                hover = None if label_col == "(none)" else label_col

                gantt = build_gantt(
                    df_sched,
                    start_col=start_col,
                    end_col=end_col,
                    y_col=y_col,
                    time_mode=time_mode,
                    unit=unit,
                    base_date=base_date,
                    label_col=hover,
                )
                st.subheader("Gantt chart")
                st.plotly_chart(gantt, use_container_width=True)
            except Exception as e:
                st.error(f"Gantt build failed: {e}")

        st.subheader("Scheduled table")
        st.dataframe(df_sched, use_container_width=True, height=520)
