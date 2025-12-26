import json
import datetime as dt

import pandas as pd
import streamlit as st

from scheduler_core import (
    run_scheduler,
    bom_data as DEFAULT_BOM,
    customer_orders as DEFAULT_ORDERS,
    work_center_capacity as DEFAULT_CAPACITY,
    DEFAULT_RAW_MATERIALS,
)

# --------------------
# PAGE SETUP / THEME
# --------------------
st.set_page_config(page_title="Mini Manufacturing Scheduler", layout="wide")

st.markdown(
    """
<style>
/* Make it feel less like a notebook */
.block-container { padding-top: 1.1rem; padding-bottom: 2rem; }
h1, h2, h3 { letter-spacing: -0.02em; }
div[data-testid="stMetric"] { border: 1px solid rgba(49, 51, 63, 0.12); padding: 12px; border-radius: 14px; }
div[data-testid="stTabs"] button { font-weight: 650; }
.small-muted { color: rgba(49, 51, 63, 0.65); font-size: 0.95rem; }
hr { margin: 0.7rem 0 1rem 0; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("Mini Manufacturing Scheduler")
st.markdown(
    '<div class="small-muted">Edit inputs as clean tables → run scheduler → review Gantt + tables.</div>',
    unsafe_allow_html=True,
)
st.divider()


# --------------------
# HELPERS
# --------------------
def to_arrow_safe_df(rows):
    """Convert list[dict] or DataFrame to a PyArrow-safe pandas DataFrame."""
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
    df2 = df.copy()
    if "workcenter" not in df2.columns or "capacity" not in df2.columns:
        return {}
    df2 = df2.dropna(subset=["workcenter"])
    out = {}
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


def rm_df_default(bom_default_df: pd.DataFrame) -> pd.DataFrame:
    """Prefill RM with part_type == RW if DEFAULT_RAW_MATERIALS is empty."""
    try:
        if DEFAULT_RAW_MATERIALS and len(DEFAULT_RAW_MATERIALS) > 0:
            return pd.DataFrame(DEFAULT_RAW_MATERIALS)

        if "part_type" in bom_default_df.columns and "part_name" in bom_default_df.columns:
            rw = (
                bom_default_df.loc[bom_default_df["part_type"].astype(str).str.upper() == "RW", "part_name"]
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
    if "scheduled" not in st.session_state:
        st.session_state["scheduled"] = None
    if "work_orders" not in st.session_state:
        st.session_state["work_orders"] = None
    if "plan" not in st.session_state:
        st.session_state["plan"] = None
    if "fig" not in st.session_state:
        st.session_state["fig"] = None


ensure_session_defaults()


def reset_to_defaults():
    st.session_state["orders_df"] = pd.DataFrame(DEFAULT_ORDERS)
    st.session_state["bom_df"] = pd.DataFrame(DEFAULT_BOM)
    st.session_state["cap_df"] = capacity_df_from_obj(DEFAULT_CAPACITY)
    st.session_state["raw_df"] = rm_df_default(st.session_state["bom_df"])
    st.session_state["scheduled"] = None
    st.session_state["work_orders"] = None
    st.session_state["plan"] = None
    st.session_state["fig"] = None


# --------------------
# SIDEBAR CONTROLS
# --------------------
with st.sidebar:
    st.header("Controls")
    show_chart = st.toggle("Show Gantt chart", value=True)
    st.caption("Tip: start with defaults, then tweak capacity and due dates.")
    run = st.button("Run scheduler", type="primary", use_container_width=True)
    st.button("Reset inputs to defaults", on_click=reset_to_defaults, use_container_width=True)

    with st.expander("Advanced: export JSON"):
        st.write("Orders")
        st.code(json.dumps(st.session_state["orders_df"].to_dict(orient="records"), indent=2))
        st.write("BOM")
        st.code(json.dumps(st.session_state["bom_df"].to_dict(orient="records"), indent=2))
        st.write("Capacity")
        st.code(json.dumps(capacity_obj_from_df(st.session_state["cap_df"]), indent=2))
        st.write("Raw materials")
        st.code(json.dumps(st.session_state["raw_df"].to_dict(orient="records"), indent=2))


# --------------------
# MAIN TABS
# --------------------
tab_inputs, tab_results = st.tabs(["⚙️ Inputs", "📈 Results"])


# --------------------
# INPUTS (WITH SUB-TABS)
# --------------------
with tab_inputs:
    st.markdown("### Inputs")
    st.markdown('<div class="small-muted">Each tab is an editable table. Add/delete rows like a spreadsheet.</div>', unsafe_allow_html=True)
    st.divider()

    sub_orders, sub_bom, sub_cap, sub_rm = st.tabs(
        ["🧾 Orders", "🧩 BOM / Routing", "🏭 Capacity", "🧱 Raw Materials"]
    )

    with sub_orders:
        st.subheader("Customer orders")
        st.session_state["orders_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["orders_df"]),
            use_container_width=True,
            num_rows="dynamic",
            key="orders_editor",
        )
        st.caption("Columns expected: order_number, customer, product, quantity, due_date (YYYY-MM-DD).")

    with sub_bom:
        st.subheader("BOM / routing data")
        st.session_state["bom_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["bom_df"]),
            use_container_width=True,
            num_rows="dynamic",
            height=520,
            key="bom_editor",
        )
        st.caption("Uses your schema: part_name, inputs_needed (comma-separated), stepnumber, workcenter, batchsize, cycletime, etc.")

    with sub_cap:
        st.subheader("Work-center capacity")
        st.session_state["cap_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["cap_df"]),
            use_container_width=True,
            num_rows="dynamic",
            height=360,
            key="cap_editor",
        )
        st.caption("Capacity is number of parallel tools/resources available per workcenter.")

    with sub_rm:
        st.subheader("Raw materials")
        st.session_state["raw_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["raw_df"]),
            use_container_width=True,
            num_rows="dynamic",
            height=320,
            key="raw_editor",
        )
        st.caption("Right now this is a declared list. Next step (optional) is inventory/shortage constraints.")


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
        st.info("Run the scheduler from the sidebar to see results.")
    else:
        # Top metrics bar
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        c1.metric("Scheduled rows", len(scheduled))
        c2.metric("Work orders", len(work_orders) if work_orders else 0)
        c3.metric("Ledger rows", len(plan.get("ledger", [])) if plan else 0)

        inferred = plan.get("raw_materials_inferred", []) if plan else []
        declared = plan.get("raw_materials", []) if plan else []
        c4.metric("Raw materials", f"{len(declared)} declared")

        st.divider()

        # Gantt
        if show_chart:
            if fig is not None:
                st.subheader("Gantt chart")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Gantt chart was not generated (fig is None). If you expect one, check scheduler_core chart generation + Plotly dependency.")

        # Tables
        st.subheader("Scheduled table")
        st.dataframe(to_arrow_safe_df(scheduled), use_container_width=True, height=440)

        colA, colB = st.columns(2)
        with colA:
            with st.expander("Work orders", expanded=False):
                st.dataframe(to_arrow_safe_df(work_orders), use_container_width=True, height=360)
        with colB:
            with st.expander("Plan ledger", expanded=False):
                st.dataframe(to_arrow_safe_df(plan.get("ledger", [])), use_container_width=True, height=360)

        with st.expander("Raw materials (inferred vs declared)", expanded=False):
            st.write("**Inferred from BOM:**")
            st.code(json.dumps(inferred, indent=2))
            st.write("**Declared:**")
            st.dataframe(to_arrow_safe_df(declared), use_container_width=True, height=240)
