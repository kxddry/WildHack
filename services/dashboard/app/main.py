"""Streamlit multipage app entry point for WildHack Transport Dispatcher."""

import streamlit as st

st.set_page_config(
    page_title="WildHack Transport Dispatcher",
    page_icon="\U0001f69b",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global styling
st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] {
        font-size: 1.6rem;
        font-weight: 700;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.9rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #999;
    }
    div[data-testid="stSidebar"] {
        padding-top: 1rem;
    }
    .stDivider {
        margin: 0.5rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.sidebar.title("\U0001f69b Transport Dispatcher")
st.sidebar.caption("WildHack -- Automated Dispatch System")

page = st.sidebar.radio(
    "Navigation",
    ["Overview", "Forecasts", "Dispatch", "Quality"],
    label_visibility="collapsed",
)

st.sidebar.divider()

if page == "Overview":
    from app.pages.overview import render
    render()
elif page == "Forecasts":
    from app.pages.forecast import render
    render()
elif page == "Dispatch":
    from app.pages.dispatch import render
    render()
elif page == "Quality":
    from app.pages.quality import render
    render()
