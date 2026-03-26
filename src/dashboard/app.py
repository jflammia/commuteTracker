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
