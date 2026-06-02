"""
Minimal test: cross-subplot vertical spike lines in Plotly + Streamlit.
Run: streamlit run test_spike.py
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

st.title("Cross-subplot spike line test")

dates = pd.date_range("2024-01-01", periods=100, freq="B")
y1 = np.cumsum(np.random.randn(100)) + 100
y2 = np.random.rand(100)
y3 = np.random.rand(100) * 2 + 0.5

# --- Test 1: shared_xaxes + spikemode="across" ---
st.subheader("Test 1: shared_xaxes=True, spikemode='across'")
fig1 = make_subplots(rows=3, cols=1, shared_xaxes=True,
                     row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.05)
fig1.add_trace(go.Scatter(x=dates, y=y1, name="price"), row=1, col=1)
fig1.add_trace(go.Scatter(x=dates, y=y2, name="ind1"), row=2, col=1)
fig1.add_trace(go.Scatter(x=dates, y=y3, name="ind2"), row=3, col=1)
fig1.update_layout(hovermode="x unified", height=500, template="plotly_dark")
fig1.update_xaxes(showspikes=True, spikemode="across",
                  spikecolor="yellow", spikethickness=1, spikesnap="cursor")
fig1.update_yaxes(showspikes=True, spikecolor="yellow",
                  spikethickness=1, spikesnap="cursor")
st.plotly_chart(fig1, use_container_width=True)

# --- Test 2: WITHOUT shared_xaxes (each row has independent axis) ---
st.subheader("Test 2: shared_xaxes=False")
fig2 = make_subplots(rows=3, cols=1, shared_xaxes=False,
                     row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.05)
fig2.add_trace(go.Scatter(x=dates, y=y1, name="price"), row=1, col=1)
fig2.add_trace(go.Scatter(x=dates, y=y2, name="ind1"), row=2, col=1)
fig2.add_trace(go.Scatter(x=dates, y=y3, name="ind2"), row=3, col=1)
fig2.update_layout(hovermode="x unified", height=500, template="plotly_dark")
fig2.update_xaxes(showspikes=True, spikemode="across",
                  spikecolor="yellow", spikethickness=1, spikesnap="cursor")
fig2.update_yaxes(showspikes=True, spikecolor="yellow",
                  spikethickness=1, spikesnap="cursor")
st.plotly_chart(fig2, use_container_width=True)

# --- Test 3: shared_xaxes + hovermode="x" (not unified) ---
st.subheader("Test 3: shared_xaxes=True, hovermode='x'")
fig3 = make_subplots(rows=3, cols=1, shared_xaxes=True,
                     row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.05)
fig3.add_trace(go.Scatter(x=dates, y=y1, name="price"), row=1, col=1)
fig3.add_trace(go.Scatter(x=dates, y=y2, name="ind1"), row=2, col=1)
fig3.add_trace(go.Scatter(x=dates, y=y3, name="ind2"), row=3, col=1)
fig3.update_layout(hovermode="x", height=500, template="plotly_dark")
fig3.update_xaxes(showspikes=True, spikemode="across",
                  spikecolor="yellow", spikethickness=1, spikesnap="cursor")
fig3.update_yaxes(showspikes=True, spikecolor="yellow",
                  spikethickness=1, spikesnap="cursor")
st.plotly_chart(fig3, use_container_width=True)
