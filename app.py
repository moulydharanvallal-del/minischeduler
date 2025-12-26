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

st.set_page_config(page_title="Mini Manufacturing Scheduler", layout="wide")

st.title("Mini Manufacturing Scheduler")
st.caption("Edit inputs as tables, run the scheduler, and share the app with others.")


def to_arrow_safe_df(rows):
    """
    Convert list[dict] or DataFrame-like objects to a PyArrow-safe pandas DataFrame.
    Streamlit uses PyArrow internally for st.dataframe/st.data_editor.
    """
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
    """
    If DEFAULT_RAW_MATERIALS is empty, give a nicer starter:
    - If BOM has part_type == 'RW', prefill those as raw materials.
    Otherwise, keep empty.
    """
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


with st.sidebar:
    st.header("Run")
    show_chart = st.checkbox("Show Gantt chart", value=True)
    run = st.button("Run scheduler", type="primary")

tab_inputs, tab_results, tab_share = st.tabs(["Inputs", "Results", "How to share"])

# --------------------
# INPUTS TAB (CLEAN TABLES)
# --------------------
with tab_inputs:
    # Build default dataframes (and keep them in session_state so they persist)
    if "orders_df" not in st.session_state:
        st.session_state["orders_df"] = pd.DataFrame(DEFAULT_ORDERS)

    if "bom_df" not in st.session_state:
        st.session_state["bom_df"] = pd.DataFrame(DEFAULT_BOM)

    if "cap_df" not in st.session_state:
        st.session_state["cap_df"] = capacity_df_from_obj(DEFAULT_CAPACITY)

    if "raw_df" not in st.session_state:
        st.session_state["raw_df"] = rm_df_default(st.session_state["bom_df"])

    colL, colR = st.columns([1.2, 1])

    with colL:
        st.subheader("Customer orders")
        st.session_state["orders_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["orders_df"]),
            use_container_width=True,
            num_rows="dynamic",
            key="orders_editor",
        )

        st.subheader("BOM / routing data")
        st.session_state["bom_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["bom_df"]),
            use_container_width=True,
            num_rows="dynamic",
            height=420,
            key="bom_editor",
        )

    with colR:
        st.subheader("Work-center capacity")
        st.session_state["cap_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["cap_df"]),
            use_container_width=True,
            num_rows="dynamic",
            height=260,
            key="cap_editor",
        )

        st.subheader("Raw materials")
        st.session_state["raw_df"] = st.data_editor(
            to_arrow_safe_df(st.session_state["raw_df"]),
            use_container_width=True,
            num_rows="dynamic",
            height=220,
            key="raw_editor",
        )

        st.info("Tip: edit like a spreadsheet. Use the Advanced section only if you want raw JSON.")

    with st.expander("Advanced: view / paste JSON"):
        st.write("Customer orders JSON")
        st.code(json.dumps(st.session_state["orders_df"].to_dict(orient="records"), indent=2))

        st.write("BOM JSON")
        st.code(json.dumps(st.session_state["bom_df"].to_dict(orient="records"), indent=2))

        st.write("Capacity JSON")
        st.code(json.dumps(capacity_obj_from_df(st.session_state["cap_df"]), indent=2))

        st.write("Raw materials JSON")
        st.code(json.dumps(st.session_state["raw_df"].to_dict(orient="records"), indent=2))


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
# RESULTS TAB
# --------------------
with tab_results:
    scheduled = st.session_state.get("scheduled")
    work_orders = st.session_state.get("work_orders")
    plan = st.session_state.get("plan")
    fig = st.session_state.get("fig")

    if not scheduled:
        st.warning("Run the scheduler from the sidebar to see results.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Scheduled rows", len(scheduled))
        c2.metric("Work orders", len(work_orders) if work_orders else 0)
        c3.metric("Ledger rows", len(plan.get("ledger", [])) if plan else 0)

        inferred = plan.get("raw_materials_inferred", []) if plan else []
        declared = plan.get("raw_materials", []) if plan else []
        st.caption(f"Raw materials — inferred from BOM: {len(inferred)} | declared: {len(declared)}")

        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("Raw materials (inferred vs declared)"):
            st.write("**Inferred from BOM:**")
            st.code(json.dumps(inferred, indent=2))
            st.write("**Declared:**")
            st.dataframe(to_arrow_safe_df(declared), use_container_width=True, height=220)

        st.subheader("Scheduled table")
        st.dataframe(to_arrow_safe_df(scheduled), use_container_width=True, height=360)

        with st.expander("Work orders"):
            st.dataframe(to_arrow_safe_df(work_orders), use_container_width=True, height=300)

        with st.expander("Plan ledger"):
            st.dataframe(to_arrow_safe_df(plan.get("ledger", [])), use_container_width=True, height=300)


# --------------------
# SHARE TAB
# --------------------
with tab_share:
    st.subheader("Share options (lightweight)")
    st.markdown(
        "**Option A (easiest): Streamlit Community Cloud**\n"
        "1. Put these files in a GitHub repo\n"
        "2. In Streamlit Cloud, deploy `app.py`\n"
        "3. Share the URL\n\n"
        "**Option B (internal): run locally**\n"
        "```bash\n"
        "python -m venv .venv\n"
        "source .venv/bin/activate\n"
        "pip install -r requirements.txt\n"
        "streamlit run app.py\n"
        "```\n\n"
        "**Option C (single binary): PyInstaller**\n"
        "```bash\n"
        "pip install pyinstaller\n"
        "pyinstaller --onefile --noconsole app.py\n"
        "```"
    )
