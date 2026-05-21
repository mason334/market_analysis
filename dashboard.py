from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from market_analysis.db.queries import fetch_signals_range

st.set_page_config(page_title="Market Analysis", layout="wide")
st.title("Market Analysis — Signal Dashboard")

# Sidebar controls
with st.sidebar:
    st.header("Filters")
    end_date = st.date_input("End date", value=date.today())
    lookback = st.slider("Lookback days", min_value=1, max_value=30, value=7)
    start_date = end_date - timedelta(days=lookback)
    strategy_filter = st.selectbox(
        "Strategy",
        options=["All", "sudden_surge", "ma_support"],
        index=0,
    )
    signal_type_filter = st.selectbox(
        "Signal type",
        options=["All", "bullish", "bearish"],
        index=0,
    )

# Fetch data
df = fetch_signals_range(start_date, end_date)

if df.empty:
    st.info(f"No signals found between {start_date} and {end_date}.")
    st.stop()

# Apply filters
if strategy_filter != "All":
    df = df[df["strategy"] == strategy_filter]
if signal_type_filter != "All":
    df = df[df["signal_type"] == signal_type_filter]

# Summary metrics
col1, col2, col3 = st.columns(3)
col1.metric("Total signals", len(df))
col2.metric("Symbols", df["symbol"].nunique())
col3.metric("Strategies", df["strategy"].nunique())

st.divider()

# Main table
st.subheader("Signal Records")
display_cols = ["date", "symbol", "strategy", "signal_type", "detail_json"]
st.dataframe(
    df[display_cols].sort_values(["date", "symbol"], ascending=[False, True]),
    use_container_width=True,
    hide_index=True,
)

# Breakdown by strategy
st.subheader("Signals by Strategy")
st.bar_chart(df.groupby("strategy").size())

# Breakdown by date
st.subheader("Signals by Date")
st.bar_chart(df.groupby(df["date"].astype(str)).size())
