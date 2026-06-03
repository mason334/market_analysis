from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_db_table_columns,
    fetch_db_table_data,
    fetch_db_table_names,
    fetch_db_table_row_count,
    fetch_indicators_daily_by_date,
    fetch_indicators_daily_for_symbol,
    fetch_latest_indicators_daily_date,
    fetch_latest_indicators_daily_for_symbol,
    fetch_ohlcv,
)
from market_analysis.strategies.support_resistance import (
    compute_raw_swings,
    compute_sr_levels,
)

st.set_page_config(page_title="Market Analysis", layout="wide", page_icon="📈")

# ---------------------------------------------------------------------------
# User preferences persistence
# ---------------------------------------------------------------------------
_PREFS_FILE = Path(__file__).parent / "config" / "user_prefs.yaml"


def _load_user_prefs() -> dict[str, Any]:
    if _PREFS_FILE.exists():
        with _PREFS_FILE.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_user_prefs(section: str, values: dict[str, Any]) -> None:
    prefs = _load_user_prefs()
    prefs[section] = values
    _PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _PREFS_FILE.open("w", encoding="utf-8") as f:
        yaml.dump(prefs, f, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
if "page" not in st.session_state:
    st.session_state.page = "overview"
    st.session_state.selected_symbol = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_STATUS_SORT: dict[str, int] = {
    "at_resistance": 0,
    "at_support": 1,
    "warning": 2,
    "watch": 3,
    "normal": 4,
}

_STATUS_LABEL: dict[str, str] = {
    "at_support": "🔵 在支撑位",
    "at_resistance": "🔴 在阻力位",
    "warning": "⚠️ 警告",
    "watch": "👀 关注",
    "normal": "",
}

_BREAKOUT_LABEL: dict[str | None, str] = {
    "break_up": "🟢 向上突破",
    "break_down": "🔴 向下跌破",
    None: "",
}

_SR_COLORS = {
    "support":    {"line": "#2196f3", "fill": "rgba(33,150,243,0.08)"},
    "resistance": {"line": "#ef5350", "fill": "rgba(239,83,80,0.08)"},
    "both":       {"line": "#ffd600", "fill": "rgba(255,214,0,0.08)"},
}


# ---------------------------------------------------------------------------
# Cached computations
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _compute_sr_levels(
    symbol: str,
    swing_order: int,
    cluster_pct: float,
    min_touches: int,
    min_span_days: int,
    max_levels: int,
    max_dist_pct: float,
    extreme_order: int,
    extreme_pct: float,
    lookback_window: int,
) -> list[dict]:
    df = fetch_ohlcv(symbol)
    if df.empty:
        return []
    params = {
        "swing_order": swing_order,
        "cluster_pct": cluster_pct,
        "min_touches": min_touches,
        "min_span_days": min_span_days,
        "max_levels": max_levels,
        "max_dist_pct": max_dist_pct,
        "extreme_order": extreme_order,
        "extreme_pct": extreme_pct,
        "lookback_window": lookback_window,
    }
    return compute_sr_levels(df, params)


@st.cache_data(ttl=600, show_spinner=False)
def _compute_raw_swings(symbol: str, swing_order: int, lookback_window: int) -> tuple[list, list]:
    df = fetch_ohlcv(symbol)
    if df.empty:
        return [], []
    return compute_raw_swings(df, swing_order=swing_order, lookback_window=lookback_window)


@st.cache_data(ttl=600, show_spinner="读取行情数据...")
def _fetch_ohlcv_cached(symbol: str) -> pd.DataFrame:
    return fetch_ohlcv(symbol)


_UNIVERSE_FILE = Path(__file__).parent / "config" / "universe.yaml"
_USER_PREFS_FILE = Path(__file__).parent / "config" / "user_prefs.yaml"


def _load_sr_params() -> dict:
    """Load SR params: settings.yaml defaults overridden by user_prefs.yaml sr section."""
    base = dict(settings.strategies.get("support_resistance", {}))
    if _USER_PREFS_FILE.exists():
        with _USER_PREFS_FILE.open(encoding="utf-8") as f:
            prefs = yaml.safe_load(f) or {}
        base.update(prefs.get("sr", {}))
    return base

_INDICATORS_DAILY_COLS = [
    "symbol", "date",
    "nearest_support", "nearest_resistance",
    "dist_support_pct", "dist_support_atr",
    "dist_resistance_pct", "dist_resistance_atr",
    "atr_14", "sr_status",
    "breakout_5d", "breakout_level",
    "trend_slope_5d", "trend_r2_5d",
]




# ---------------------------------------------------------------------------
# Overview page
# ---------------------------------------------------------------------------
def show_overview() -> None:
    st.title("📈 Market Analysis — 日常快照")

    latest_db_date = fetch_latest_indicators_daily_date()
    default_date = latest_db_date if latest_db_date is not None else date.today()

    with st.sidebar:
        st.header("筛选")
        selected_date = st.date_input("日期", value=default_date)
        if latest_db_date is not None and latest_db_date < date.today():
            st.caption(f"最新数据：{latest_db_date}（今日数据待更新）")
        st.divider()
        if st.button("🔄 刷新数据"):
            st.cache_data.clear()
            st.rerun()

    df = fetch_indicators_daily_by_date(selected_date)

    if df.empty:
        st.warning(
            f"**{selected_date}** 暂无数据。\n\n"
            "请运行以下命令生成分析并写入数据库：\n"
            "```\nmarket-analysis run\n```"
        )
        return

    # Metrics bar
    n_symbols = len(df)
    n_at_sr = int(df["sr_status"].isin(["at_support", "at_resistance"]).sum())
    n_warning = int((df["sr_status"] == "warning").sum())
    n_breakout = int(df["breakout_5d"].notna().sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("扫描股票数", n_symbols)
    c2.metric("在SR位", n_at_sr)
    c3.metric("警告", n_warning)
    c4.metric("近5日突破", n_breakout)

    st.divider()

    # Build display table
    display = df.copy()
    display["_sort"] = display["sr_status"].map(lambda s: _STATUS_SORT.get(s, 99))
    display = display.sort_values(["_sort", "symbol"], ignore_index=True)
    display["状态"] = display["sr_status"].map(lambda s: _STATUS_LABEL.get(s, s))
    display["突破(5d)"] = display["breakout_5d"].map(lambda v: _BREAKOUT_LABEL.get(v, ""))
    display["斜率(5d)"] = display["trend_slope_5d"].map(
        lambda v: f"{v*100:+.3f}%" if pd.notna(v) else ""
    )
    display["R²"] = display["trend_r2_5d"].map(
        lambda v: f"{v:.2f}" if pd.notna(v) else ""
    )
    display["距支撑(ATR)"] = display["dist_support_atr"].map(
        lambda v: f"{v:.2f}" if pd.notna(v) else "—"
    )
    display["距阻力(ATR)"] = display["dist_resistance_atr"].map(
        lambda v: f"{v:.2f}" if pd.notna(v) else "—"
    )
    display["支撑价"] = display["nearest_support"].map(
        lambda v: f"{v:.2f}" if pd.notna(v) else "—"
    )
    display["阻力价"] = display["nearest_resistance"].map(
        lambda v: f"{v:.2f}" if pd.notna(v) else "—"
    )

    show_cols = ["symbol", "状态", "支撑价", "距支撑(ATR)", "阻力价", "距阻力(ATR)", "突破(5d)", "斜率(5d)", "R²"]
    display_df = display[show_cols].copy()

    st.subheader(f"{selected_date} 分析快照")
    st.caption("点击任意行可查看该股票详情")

    event = st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    if event.selection and event.selection.rows:
        row_idx = event.selection.rows[0]
        symbol = str(display.iloc[row_idx]["symbol"])
        st.session_state.selected_symbol = symbol
        st.session_state.page = "detail"
        st.rerun()


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------
def show_detail(symbol: str) -> None:
    if st.button("← 返回总览"):
        st.session_state.page = "overview"
        st.session_state.selected_symbol = None
        st.rerun()

    st.title(f"📊 {symbol} — 详情")

    # Latest snapshot from DB
    latest = fetch_latest_indicators_daily_for_symbol(symbol)

    if latest:
        status = latest.get("sr_status", "normal")
        _fmt = lambda v: f"{v:.2f}" if v is not None else "—"
        if status == "at_support":
            st.info(
                f"🔵 **在支撑位** — 支撑价 **{latest['nearest_support']}**，"
                f"ATR距离 **{_fmt(latest.get('dist_support_atr'))}**"
            )
        elif status == "at_resistance":
            st.warning(
                f"🔴 **在阻力位** — 阻力价 **{latest['nearest_resistance']}**，"
                f"ATR距离 **{_fmt(latest.get('dist_resistance_atr'))}**"
            )
        elif status == "warning":
            _atrs = [v for v in [latest.get("dist_support_atr"), latest.get("dist_resistance_atr")] if v is not None]
            nearest_atr = min(_atrs) if _atrs else None
            st.warning(f"⚠️ **警告** — 接近关键位，ATR距离 **{_fmt(nearest_atr)}**")
        elif status == "watch":
            st.info("👀 **关注** — 距关键位较近")

        if latest.get("breakout_5d"):
            label = _BREAKOUT_LABEL.get(latest["breakout_5d"], latest["breakout_5d"])
            st.info(f"{label} — 突破价位 **{latest['breakout_level']}**")

    _sr_defaults = _load_sr_params()
    with st.sidebar:
        st.header("时间范围")
        if "range_saved" not in st.session_state:
            _prefs_range = _load_user_prefs().get("range", {})
            st.session_state["range_saved"] = {
                "lookback": _prefs_range.get("lookback", 250),
            }
        _rv = st.session_state["range_saved"]
        lookback = st.slider("历史天数 K线根数", min_value=30, max_value=700, value=_rv["lookback"], step=10)
        if st.button("保存参数", use_container_width=True, key="save_range"):
            st.session_state["range_saved"] = {"lookback": lookback}
            _save_user_prefs("range", {"lookback": lookback})
            st.success("参数已保存")

        st.divider()
        with st.expander("支撑阻力位参数", expanded=False):
            if "sr_saved" not in st.session_state:
                _prefs_sr = _load_user_prefs().get("sr", {})
                st.session_state["sr_saved"] = {
                    "swing_order":   int(_prefs_sr.get("swing_order",   _sr_defaults.get("swing_order", 3))),
                    "cluster_pct":   float(_prefs_sr.get("cluster_pct", _sr_defaults.get("cluster_pct", 1.5))),
                    "min_touches":   int(_prefs_sr.get("min_touches",   _sr_defaults.get("min_touches", 2))),
                    "min_span_days": int(_prefs_sr.get("min_span_days", _sr_defaults.get("min_span_days", 10))),
                    "max_levels":    int(_prefs_sr.get("max_levels",    _sr_defaults.get("max_levels", 5))),
                    "max_dist_pct":  float(_prefs_sr.get("max_dist_pct", _sr_defaults.get("max_dist_pct", 15.0))),
                    "extreme_order": int(_prefs_sr.get("extreme_order", _sr_defaults.get("extreme_order", 60))),
                    "extreme_pct":   float(_prefs_sr.get("extreme_pct", _sr_defaults.get("extreme_pct", 5.0))),
                    "lookback_window": int(_prefs_sr.get("lookback_window", _sr_defaults.get("lookback_window", 500))),
                }
            _sv = st.session_state["sr_saved"]

            sr_swing_order = st.slider("摆动点灵敏度 (swing_order)", 1, 50, _sv["swing_order"],
                help="极值点左右各需要 N 根 K 线配合")
            sr_cluster_pct = st.slider("聚类宽松度 % (cluster_pct)", 0.5, 5.0, _sv["cluster_pct"], step=0.5,
                help="两个极值价格差 ≤ N% 归为同一水平区")
            sr_min_touches = st.slider("最少触及次数 (min_touches)", 2, 8, _sv["min_touches"])
            sr_min_span = st.slider("最短时间跨度 天 (min_span_days)", 5, 90, _sv["min_span_days"], step=5)
            sr_max_levels = st.slider("最多显示条数 (max_levels)", 1, 10, _sv["max_levels"])
            sr_max_dist_pct = st.slider("最远距离过滤 % (max_dist_pct)", 5, 50, int(_sv["max_dist_pct"]), step=5)
            st.caption("极端单点参数")
            sr_extreme_order = st.slider("极端位检测窗口 (extreme_order)", 10, 120, int(_sv["extreme_order"]), step=10)
            sr_extreme_pct = st.slider("极端位偏离阈值 % (extreme_pct)", 1.0, 20.0, float(_sv["extreme_pct"]), step=1.0)
            sr_lookback = st.slider("历史回溯窗口 根K线 (lookback_window)", 100, 1500, _sv["lookback_window"], step=50)

            if st.button("保存参数", use_container_width=True):
                _sr_vals = {
                    "swing_order": sr_swing_order, "cluster_pct": sr_cluster_pct,
                    "min_touches": sr_min_touches, "min_span_days": sr_min_span,
                    "max_levels": sr_max_levels, "max_dist_pct": float(sr_max_dist_pct),
                    "extreme_order": sr_extreme_order, "extreme_pct": sr_extreme_pct,
                    "lookback_window": sr_lookback,
                }
                st.session_state["sr_saved"] = _sr_vals
                _save_user_prefs("sr", _sr_vals)
                st.success("参数已保存")

    # Fetch OHLCV
    ohlcv = _fetch_ohlcv_cached(symbol)
    if ohlcv.empty:
        st.error(f"未找到 {symbol} 的 OHLCV 数据。")
        return

    ohlcv_range = ohlcv.iloc[-lookback:]

    # Build K-line chart
    fig = go.Figure()

    fig.add_trace(go.Candlestick(
        x=ohlcv_range.index,
        open=ohlcv_range["open"],
        high=ohlcv_range["high"],
        low=ohlcv_range["low"],
        close=ohlcv_range["close"],
        name="K线",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
        yaxis="y",
    ))

    # SR levels
    sr_levels = _compute_sr_levels(
        symbol, sr_swing_order, sr_cluster_pct,
        sr_min_touches, sr_min_span, sr_max_levels, sr_max_dist_pct,
        sr_extreme_order, sr_extreme_pct, sr_lookback,
    )
    _chart_x0 = ohlcv_range.index[0]
    _chart_x1 = ohlcv_range.index[-1]
    _price_min = float(ohlcv_range["low"].min())
    _price_max = float(ohlcv_range["high"].max())

    for lv in sr_levels:
        _last_swing = max(lv["swing_dates"])
        _out_of_range = _last_swing < _chart_x0   # all swing points older than chart view
        if lv["zone_high"] < _price_min * 0.95 or lv["zone_low"] > _price_max * 1.05:
            continue
        _ltype = lv["level_type"]
        _col = _SR_COLORS[_ltype]
        _is_extreme = lv.get("is_extreme", False)

        if _is_extreme or _out_of_range:
            # Extreme levels and historical levels: draw as full-width reference line
            fig.add_shape(
                type="line", x0=_chart_x0, x1=_chart_x1, xref="x",
                y0=lv["price"], y1=lv["price"], yref="y",
                line=dict(color=_col["line"], width=2 if _is_extreme else 1.5,
                          dash="solid" if _is_extreme else "dot"),
            )
            _suffix = "★" if _is_extreme else f"×{lv['touches']} (历史)"
            fig.add_annotation(
                x=_chart_x1, xref="x", y=lv["price"], yref="y",
                text=f"  {lv['price']} {_suffix}",
                showarrow=False, xanchor="left",
                font=dict(size=10, color=_col["line"]),
            )
        else:
            _x0 = max(min(lv["swing_dates"]), _chart_x0)
            _x1 = _last_swing
            fig.add_shape(
                type="rect",
                x0=_x0, x1=_x1, xref="x",
                y0=lv["zone_low"], y1=lv["zone_high"], yref="y",
                fillcolor=_col["fill"], line_width=0,
            )
            fig.add_shape(
                type="line", x0=_x0, x1=_x1, xref="x",
                y0=lv["price"], y1=lv["price"], yref="y",
                line=dict(color=_col["line"], width=1.5, dash="dash"),
            )
            fig.add_annotation(
                x=_x1, xref="x", y=lv["price"], yref="y",
                text=f"  {lv['price']} ×{lv['touches']}",
                showarrow=False, xanchor="left",
                font=dict(size=10, color=_col["line"]),
            )

        # Swing point markers (only for levels within chart range)
        _sdates = [] if _out_of_range else [d for d in lv["swing_dates"] if d in ohlcv_range.index]
        _sprices = [
            lv["swing_prices"][lv["swing_dates"].index(d)] for d in _sdates
        ]
        if _sdates:
            fig.add_trace(go.Scatter(
                x=_sdates, y=_sprices, mode="markers",
                marker=dict(
                    symbol="star-triangle-up" if _is_extreme else "diamond",
                    size=10 if _is_extreme else 8,
                    color=_col["line"], opacity=0.9 if _is_extreme else 0.85,
                    line=dict(width=1, color="white"),
                ),
                showlegend=False,
                hovertemplate=f"{'极端位' if _is_extreme else 'SR'} {lv['price']}<br>%{{x|%Y-%m-%d}}<br>%{{y:.2f}}<extra></extra>",
                yaxis="y",
            ))

    # Filtered-out swing points (gray circles)
    _all_swing_dates, _all_swing_prices = _compute_raw_swings(symbol, sr_swing_order, sr_lookback)
    _adopted_dates: set = set()
    for lv in sr_levels:
        _adopted_dates.update(lv["swing_dates"])
    _rej_dates = [d for d in _all_swing_dates if d not in _adopted_dates and d in ohlcv_range.index]
    _rej_prices = [_all_swing_prices[_all_swing_dates.index(d)] for d in _rej_dates]
    if _rej_dates:
        fig.add_trace(go.Scatter(
            x=_rej_dates, y=_rej_prices, mode="markers",
            marker=dict(symbol="circle", size=5, color="#90A4AE", opacity=0.55, line=dict(width=0)),
            name="未采用摆动点", showlegend=False,
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}<extra>摆动点(未采用)</extra>",
            yaxis="y",
        ))

    fig.update_layout(
        height=600,
        hovermode="x unified",
        template="plotly_dark",
        legend=dict(orientation="v", xanchor="right", x=-0.04, yanchor="top", y=1.0),
        margin=dict(l=100, r=50, t=60, b=40),
        xaxis=dict(
            showspikes=True, spikemode="across",
            spikecolor="rgba(180,180,180,0.6)", spikethickness=1,
            spikedash="dot", spikesnap="cursor",
            rangeslider=dict(visible=False),
        ),
        yaxis=dict(
            showspikes=True, spikecolor="rgba(180,180,180,0.4)",
            spikethickness=1, spikedash="dot", spikesnap="cursor", autorange=True,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Latest snapshot summary
    if latest:
        st.subheader("最新快照数据")
        snap_date = latest.get("date")
        if hasattr(snap_date, "date"):
            snap_date = snap_date.date()
        st.caption(f"数据日期：{snap_date}")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("支撑价", f"{latest['nearest_support']:.2f}" if latest.get("nearest_support") else "—")
        col1.metric("距支撑(ATR)", f"{latest['dist_support_atr']:.2f}" if latest.get("dist_support_atr") is not None else "—")
        col2.metric("阻力价", f"{latest['nearest_resistance']:.2f}" if latest.get("nearest_resistance") else "—")
        col2.metric("距阻力(ATR)", f"{latest['dist_resistance_atr']:.2f}" if latest.get("dist_resistance_atr") is not None else "—")
        col3.metric("ATR14", f"{latest['atr_14']:.2f}" if latest.get("atr_14") else "—")
        col3.metric("突破(5d)", _BREAKOUT_LABEL.get(latest.get("breakout_5d"), "") or "—")
        col4.metric("趋势斜率(5d)", f"{latest['trend_slope_5d']*100:+.3f}%" if latest.get("trend_slope_5d") is not None else "—")
        col4.metric("R²", f"{latest['trend_r2_5d']:.2f}" if latest.get("trend_r2_5d") is not None else "—")

    # Historical snapshot table
    st.subheader("历史快照")
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=90)
    hist = fetch_indicators_daily_for_symbol(symbol, start_dt, end_dt)
    if not hist.empty:
        hist_disp = hist[["date", "sr_status", "nearest_support", "nearest_resistance",
                           "dist_support_atr", "dist_resistance_atr",
                           "breakout_5d", "trend_slope_5d", "trend_r2_5d"]].copy()
        hist_disp["sr_status"] = hist_disp["sr_status"].map(lambda s: _STATUS_LABEL.get(s, s))
        hist_disp["breakout_5d"] = hist_disp["breakout_5d"].map(lambda v: _BREAKOUT_LABEL.get(v, ""))
        hist_disp["trend_slope_5d"] = hist_disp["trend_slope_5d"].map(
            lambda v: f"{v*100:+.3f}%" if pd.notna(v) else ""
        )
        hist_disp = hist_disp.rename(columns={
            "date": "日期", "sr_status": "状态",
            "nearest_support": "支撑价", "nearest_resistance": "阻力价",
            "dist_support_atr": "距支撑(ATR)", "dist_resistance_atr": "距阻力(ATR)",
            "breakout_5d": "突破(5d)", "trend_slope_5d": "斜率(5d)", "trend_r2_5d": "R²",
        })
        st.dataframe(hist_disp, use_container_width=True, hide_index=True)
    else:
        st.info("暂无历史快照数据。")


# ---------------------------------------------------------------------------
# DB Viewer page
# ---------------------------------------------------------------------------
def show_db_viewer() -> None:
    st.title("🗄️ 数据库查看器")

    with st.sidebar:
        st.header("数据库")
        db_choice = st.radio("选择数据库", options=["market_analysis", "market_data"], index=0)
        use_source = db_choice == "market_data"
        st.divider()
        if st.button("🔄 刷新"):
            st.rerun()

    try:
        tables = fetch_db_table_names(use_source=use_source)
    except Exception as e:
        st.error(f"无法连接数据库：{e}")
        return

    if not tables:
        st.info("该数据库没有任何表。")
        return

    selected_table = st.selectbox("选择表", options=tables)

    try:
        col_info = fetch_db_table_columns(selected_table, use_source=use_source)
        row_count = fetch_db_table_row_count(selected_table, use_source=use_source)
    except Exception as e:
        st.error(f"读取表结构失败：{e}")
        return

    meta_c1, meta_c2 = st.columns(2)
    meta_c1.metric("总行数", f"{row_count:,}")
    meta_c2.metric("列数", len(col_info))

    with st.expander("字段列表", expanded=False):
        st.dataframe(col_info, use_container_width=True, hide_index=True)

    st.divider()

    page_size = st.select_slider("每页行数", options=[20, 50, 100, 200, 500], value=100)
    total_pages = max(1, (row_count + page_size - 1) // page_size)
    page_num = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1)
    offset = (page_num - 1) * page_size
    st.caption(f"第 {page_num}/{total_pages} 页，共 {row_count:,} 行")

    try:
        df = fetch_db_table_data(selected_table, limit=page_size, offset=offset, use_source=use_source)
    except Exception as e:
        st.error(f"读取数据失败：{e}")
        return

    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Global navigation
# ---------------------------------------------------------------------------
_NAV_LABELS = {
    "overview": "📈 日常快照",
    "db_viewer": "🗄️ 数据库查看器",
}

with st.sidebar:
    current = st.session_state.page if st.session_state.page != "detail" else "overview"
    nav_choice = st.radio(
        "页面",
        options=list(_NAV_LABELS.keys()),
        format_func=lambda k: _NAV_LABELS[k],
        index=list(_NAV_LABELS.keys()).index(current),
        key="_global_nav",
        label_visibility="collapsed",
    )
    if nav_choice != current and st.session_state.page != "detail":
        st.session_state.page = nav_choice
        st.rerun()
    elif nav_choice != current and st.session_state.page == "detail":
        st.session_state.page = nav_choice
        st.session_state.selected_symbol = None
        st.rerun()
    st.divider()

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
if st.session_state.page == "overview":
    show_overview()
elif st.session_state.page == "db_viewer":
    show_db_viewer()
else:
    show_detail(st.session_state.selected_symbol)
