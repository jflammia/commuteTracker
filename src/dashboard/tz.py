"""Browser timezone detection for dashboard display.

Streamlit does NOT auto-adjust datetimes to the user's timezone — it renders
them exactly as provided. We must detect the browser timezone and convert
timestamps ourselves before displaying.

Usage in pages:
    from src.dashboard.tz import get_display_tz
    display_tz = get_display_tz()
"""

from __future__ import annotations

import streamlit as st

from src.config import TIMEZONE


def get_display_tz() -> str:
    """Return the browser's IANA timezone, falling back to the server config.

    Uses st.context.timezone (Streamlit >= 1.37) which reads the browser's
    Intl.DateTimeFormat().resolvedOptions().timeZone.
    """
    try:
        tz = st.context.timezone
        if tz:
            return tz
    except (AttributeError, KeyError):
        pass
    return TIMEZONE
