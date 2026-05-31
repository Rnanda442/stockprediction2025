import hmac
import os

import streamlit as st


def _configured_password():
    try:
        return st.secrets["DASHBOARD_PASSWORD"]
    except (FileNotFoundError, KeyError):
        return os.getenv("DASHBOARD_PASSWORD", "")


def require_login():
    password = _configured_password()
    if not password:
        st.error("Set DASHBOARD_PASSWORD before starting the dashboard.")
        st.stop()

    if st.session_state.get("authenticated"):
        return

    st.title("Stock Research Dashboard")
    st.caption("Private research view")
    entered = st.text_input("Password", type="password")
    if st.button("Sign in", type="primary", use_container_width=True):
        if hmac.compare_digest(entered, password):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()
