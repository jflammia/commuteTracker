"""Commute Tracker Dashboard - main entry point.

Run with: streamlit run src/dashboard/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Commute Tracker",
    page_icon="🚆",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Commute Tracker")
st.markdown(
    "Analyze your commute patterns, find bottlenecks, and optimize your schedule. "
    "Select a page from the sidebar to get started."
)

st.sidebar.success("Select a page above.")

# Build info from backend
try:
    from src.dashboard.api_client import get_health

    health = get_health()
    version = health.get("version", "unknown")
    commit = health.get("git_commit", "")
    build_label = f"Build: v{version}"
    if commit:
        build_label += f" ({commit[:7]})"
    st.sidebar.caption(build_label)
except Exception:
    st.sidebar.caption("Build: unavailable")
