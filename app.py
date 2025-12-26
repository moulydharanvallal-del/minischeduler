# app.py
import json
import datetime as dt

import pandas as pd
import streamlit as st

# Plotly is used for fallback gantt
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
st.markdown(
    '<div class="small-muted">Edit inputs → Run scheduler → Review Gantt + tables.</div>',
    unsafe_allow_html=True,
)
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


def build_fallback_gantt(scheduled_rows):
    """
    Always-attempt fallback gantt from schedule output.

    Requirements:
    - Some column like start/start_time
    - Some column like end/end_time
    - Some column like workcenter/resource
    """
    df = to_arrow_safe_df(scheduled_rows).copy()
    if df.empty:
        return None, {"error": "scheduled is empty", "columns": []}

    cols = list(df.columns)
    lower = [c.lower() for c in cols]

    def pick_col(candidates):
        for cand in candidates:
            for i, c in enumerate(lower):
                if c == cand or cand in c:
                    return cols[i]
        return None

    start_col = pick_col(["start", "start_time", "start_datetime", "start_dt", "start_date", "startdate"])
    end_col = pick_col(["end", "end_time", "end_datetime", "end_dt", "end_date", "enddate", "finish", "finish_time"])
    y_col = pick_col(["workcenter", "work_center", "resource", "wc", "machine", "tool"])
    label_col = pick_col(["order_number", "order", "so", "wo", "work_order", "product", "part", "part_name"])

    debug = {
        "detected": {"start": start_col, "end": end_col, "y": y_col, "label": label_col},
        "columns": cols,
    }

    if not (start_col and end_col and y_col):
        debug["error"] = "Could not detect required columns (start/end/y)."
        return None, debug

    # Coerce to datetime
    df[start_col] = pd.to_datetime(df[start_col], errors="coerce")
    df[end_col] = pd.to_datetime(df[end_col], errors="coerce")
    df = df.dropna(subset=[start_col, end_col, y_col])

    if df.empty:
        debug["error"] = "All rows dropped after datetime coercion (start/end not parseable)."
        return None, debug

    if label_col is None or label_col not in df.columns:
        label_col = y_col

    try:
        gantt = px.timeline(
            df,
            x_start=start_col,
            x_end=end_col,
            y=y_col,
            hover_data=[label_col] if label_col in df.columns else None,
        )
        gantt.update_yaxes(autorange="reversed")
        return gantt, debug
    except Exception as e:
        debug["error"] = f"Plotly timeline creation failed: {e}"
        return None, debug


ensure_session_defaults()

# --------------------
# SIDEBAR
# --------------------
with st.sidebar:
    st.header("Controls")
    show_chart = st.toggle("Show Gantt chart", value=True)
    run = st.button("Run scheduler", type="primary", use_container_width=True)
    st.button("Reset inputs", on_click=reset_to_defaults, use_container_width=True)

    with st.expander("Export inputs as JSON"):
        st.write("Orders")
        st.code(json.dumps(st.session_state["orders_df"].to_dict(orient="records"), indent=2))
        st.write("BOM")
        st.code(json.dumps(st.session_state["bom_df"].to_dict(orient="records"), indent=2))
        st.write("Capacity")
        st.code(json.dumps(capacity_obj_from_df(st.session_state["cap_df"]), indent=2))
        st.write("Raw materials")
        st.code(json.dumps(st.session_state["raw_df"].to_dict(orient="records"), indent=2))


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
        inferred = plan.get("raw_materials_inferred", []) if plan else []
        declared = plan.get("raw_materials", []) if plan else []

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scheduled rows", len(scheduled))
        m2.metric("Work orders", len(work_orders) if work_orders else 0)
        m3.metric("Ledger rows", len(plan.get("ledger", [])) if plan else 0)
        m4.metric("Raw materials", f"{len(declared)} declared")

        st.divider()

        # HARD check that plotly exists on Streamlit Cloud
        with st.expander("Environment debug", expanded=False):
            try:
                import plotly  # noqa: F401
                st.success("plotly import: OK")
            except Exception as e:
                st.error(f"plotly import failed: {e}")
            st.write("Python:", __import__("sys").version)

        # --------------------
        # GANTT (core fig OR fallback ALWAYS)
        # --------------------
        if show_chart:
            if fig is not None:
                st.subheader("Gantt chart")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Gantt chart was not generated by scheduler_core (fig is None). Trying fallback Gantt…")

                fallback_fig, dbg = build_fallback_gantt(scheduled)
                with st.expander("Fallback Gantt debug", expanded=True):
                    st.json(dbg)
                    st.write("Scheduled preview (first 20 rows):")
                    st.dataframe(to_arrow_safe_df(scheduled).head(20), use_container_width=True)

                if fallback_fig is not None:
                    st.subheader("Gantt chart (fallback)")
                    st.plotly_chart(fallback_fig, use_container_width=True)
                else:
                    st.error("Fallback Gantt could not be rendered. See debug above for which columns were missing/invalid.")

        # --------------------
        # TABLES
        # --------------------
        st.subheader("Scheduled table")
        st.dataframe(to_arrow_safe_df(scheduled), use_container_width=True, height=460)

        colA, colB = st.columns(2)
        with colA:
            with st.expander("Work orders"):
                st.dataframe(to_arrow_safe_df(work_orders), use_container_width=True, height=360)
        with colB:
            with st.expander("Plan ledger"):
                st.dataframe(to_arrow_safe_df(plan.get("ledger", [])), use_container_width=True, height=360)

        with st.expander("Raw materials (inferred vs declared)"):
            st.write("**Inferred from BOM:**")
            st.code(json.dumps(inferred, indent=2))
            st.write("**Declared:**")
            st.dataframe(to_arrow_safe_df(declared), use_container_width=True, height=240)
