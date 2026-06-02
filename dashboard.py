from __future__ import annotations

from datetime import date
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
    fetch_latest_snapshot_date,
    fetch_ohlcv,
    fetch_signals_by_date,
    fetch_signals_for_symbol,
    fetch_snapshots_by_date,
)
from market_analysis.strategies.hurst import hurst_history
from market_analysis.strategies.ma_support import ma_support_history
from market_analysis.strategies.markov import markov_history
from market_analysis.strategies.sharpe import sharpe_ratio_history
from market_analysis.strategies.sudden_move import sudden_move_history
from market_analysis.strategies.sudden_surge import sudden_surge_history
from market_analysis.strategies.support_resistance import compute_raw_swings, compute_sr_levels
from market_analysis.strategies.variance_ratio import variance_ratio_history

st.set_page_config(page_title="Market Analysis", layout="wide", page_icon="📈")

# ---------------------------------------------------------------------------
# User preferences persistence
# ---------------------------------------------------------------------------
_PREFS_FILE = Path(__file__).parent / "config" / "user_prefs.yaml"


def _load_user_prefs() -> dict[str, Any]:
    """Load persisted user preferences from config/user_prefs.yaml."""
    if _PREFS_FILE.exists():
        with _PREFS_FILE.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_user_prefs(section: str, values: dict[str, Any]) -> None:
    """Persist a section of user preferences to config/user_prefs.yaml."""
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
_STAT_INDICATORS = ["hurst_60d", "vr_5", "vr_10", "vr_20", "markov_regime_prob"]
_TECH_INDICATORS = ["ss_ret5d", "ma_dist_20", "ma_dist_50", "ma_dist_200",
                    "sr_dist_support", "sr_dist_resistance"]
_VOL_INDICATORS = ["ss_vol_ratio"]  # separate: scale is 1~5+, incompatible with % indicators

_INDICATOR_LABELS: dict[str, str] = {
    "hurst_60d": "Hurst(60d)",
    "vr_5": "VR(5)",
    "vr_10": "VR(10)",
    "vr_20": "VR(20)",
    "markov_regime_prob": "Markov高波动概率",
    "ss_ret5d": "5日涨幅",
    "ss_vol_ratio": "量比",
    "ma_dist_20": "距MA20",
    "ma_dist_50": "距MA50",
    "ma_dist_200": "距MA200",
    "sr_dist_support": "距支撑位",
    "sr_dist_resistance": "距阻力位",
}

# SR level visual config
_SR_COLORS = {
    "support":    {"line": "#2196f3", "fill": "rgba(33,150,243,0.08)"},
    "resistance": {"line": "#ef5350", "fill": "rgba(239,83,80,0.08)"},
    "both":       {"line": "#ffd600", "fill": "rgba(255,214,0,0.08)"},
}

_SIGNAL_EMOJI = {"bullish": "🟢", "bearish": "🔴"}


_MAX_HIST_BARS = 700  # 365-day display window + 200-bar MA200 warm-up + buffer


@st.cache_data(ttl=600, show_spinner="计算指标历史中...")
def _compute_indicator_history(symbol: str) -> pd.DataFrame:
    """Compute all indicator time-series on-demand from OHLCV (cached 10 min).

    Only uses the last _MAX_HIST_BARS rows — sufficient for the 365-day display
    window after warm-up periods, and much faster than full-history computation.

    Returns DataFrame with columns: date, indicator, value.
    """
    df = fetch_ohlcv(symbol)
    if df.empty:
        return pd.DataFrame(columns=["date", "indicator", "value"])

    if len(df) > _MAX_HIST_BARS:
        df = df.iloc[-_MAX_HIST_BARS:]

    sp = settings.strategies
    frames: list[pd.DataFrame] = []
    for fn, key in [
        (sudden_surge_history, "sudden_surge"),
        (ma_support_history, "ma_support"),
        (variance_ratio_history, "variance_ratio"),
        (hurst_history, "hurst"),
        (markov_history, "markov"),
    ]:
        try:
            hist = fn(df, sp.get(key, {}))
            if not hist.empty:
                frames.append(hist)
        except Exception:
            pass

    if not frames:
        return pd.DataFrame(columns=["date", "indicator", "value"])
    return pd.concat(frames, ignore_index=True)


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
    """Compute SR levels on-demand from full OHLCV (cached 10 min per param combo)."""
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
    """Return all raw swing points (before clustering/filtering), cached."""
    df = fetch_ohlcv(symbol)
    if df.empty:
        return [], []
    return compute_raw_swings(df, swing_order=swing_order, lookback_window=lookback_window)


@st.cache_data(ttl=600, show_spinner=False)
def _compute_sm_history(symbol: str, n: int) -> pd.DataFrame:
    """Rolling sudden_move history for given window n (cached per symbol+n)."""
    df = fetch_ohlcv(symbol)
    if df.empty:
        return pd.DataFrame(columns=["date", "indicator", "value"])
    if len(df) > _MAX_HIST_BARS:
        df = df.iloc[-_MAX_HIST_BARS:]
    return sudden_move_history(df, n)


@st.cache_data(ttl=600, show_spinner=False)
def _compute_sharpe_history(symbol: str, n: int, risk_free_rate: float) -> pd.DataFrame:
    """Rolling Sharpe ratio history for given window n (cached per symbol+n+rf)."""
    df = fetch_ohlcv(symbol)
    if df.empty:
        return pd.DataFrame(columns=["date", "indicator", "value"])
    if len(df) > _MAX_HIST_BARS:
        df = df.iloc[-_MAX_HIST_BARS:]
    return sharpe_ratio_history(df, n, risk_free_rate)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_overview_table(
    snapshots: pd.DataFrame, signals: pd.DataFrame
) -> pd.DataFrame:
    """Merge snapshots (wide) with today's signal summary."""
    if snapshots.empty and signals.empty:
        return pd.DataFrame()

    if not signals.empty:
        sig_summary = (
            signals.groupby("symbol")
            .apply(
                lambda g: ", ".join(
                    f"{_SIGNAL_EMOJI.get(row.signal_type, '')} {row.strategy}"
                    for _, row in g.iterrows()
                ),
                include_groups=False,
            )
            .reset_index()
            .rename(columns={0: "今日信号"})
        )
    else:
        sig_summary = pd.DataFrame(columns=["symbol", "今日信号"])

    if snapshots.empty:
        merged = sig_summary
    elif sig_summary.empty:
        merged = snapshots
    else:
        merged = snapshots.merge(sig_summary, on="symbol", how="outer")

    if "今日信号" not in merged.columns:
        merged["今日信号"] = "—"
    merged["今日信号"] = merged["今日信号"].fillna("—")
    return merged


# ---------------------------------------------------------------------------
# Overview page
# ---------------------------------------------------------------------------
def show_overview() -> None:
    st.title("📈 Market Analysis — 信号总览")

    latest_db_date = fetch_latest_snapshot_date()
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

    snapshots_wide = fetch_snapshots_by_date(selected_date)
    signals_df = fetch_signals_by_date(selected_date)

    # Metrics bar
    c1, c2, c3, c4 = st.columns(4)
    n_symbols = len(snapshots_wide) if not snapshots_wide.empty else 0
    n_signals = len(signals_df) if not signals_df.empty else 0
    n_bullish = int((signals_df["signal_type"] == "bullish").sum()) if not signals_df.empty else 0
    n_bearish = int((signals_df["signal_type"] == "bearish").sum()) if not signals_df.empty else 0
    c1.metric("扫描股票数", n_symbols)
    c2.metric("今日信号数", n_signals)
    c3.metric("看涨信号", n_bullish)
    c4.metric("看跌信号", n_bearish)

    st.divider()

    overview = _build_overview_table(snapshots_wide, signals_df)

    if overview.empty:
        msg = f"{selected_date} 暂无数据。"
        if latest_db_date is not None:
            msg += f" 数据库最新日期为 {latest_db_date}，请从日期选择器切换。"
        else:
            msg += " 请先运行 `market-analysis run` 生成分析。"
        st.info(msg)
        return

    # Build display columns
    display_cols = ["symbol", "今日信号"]
    for ind in _STAT_INDICATORS + _TECH_INDICATORS:
        if ind in overview.columns:
            display_cols.append(ind)

    display_df = overview[display_cols].copy()
    rename_map = {k: v for k, v in _INDICATOR_LABELS.items() if k in display_df.columns}
    display_df = display_df.rename(columns=rename_map)

    st.subheader(f"{selected_date} 指标快照")
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
        symbol = str(overview.iloc[row_idx]["symbol"])
        st.session_state.selected_symbol = symbol
        st.session_state.page = "detail"
        st.rerun()

    # Signal breakdown charts
    if not signals_df.empty:
        st.divider()
        col_l, col_r = st.columns(2)
        with col_l:
            st.subheader("按策略分布")
            st.bar_chart(signals_df.groupby("strategy").size())
        with col_r:
            st.subheader("看涨 vs 看跌")
            st.bar_chart(signals_df.groupby("signal_type").size())


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------
def show_detail(symbol: str) -> None:
    if st.button("← 返回总览"):
        st.session_state.page = "overview"
        st.session_state.selected_symbol = None
        st.rerun()

    st.title(f"📊 {symbol} — 详情")

    _sr_defaults = settings.strategies.get("support_resistance", {})
    with st.sidebar:
        st.header("时间范围")
        if "range_saved" not in st.session_state:
            _prefs_range = _load_user_prefs().get("range", {})
            st.session_state["range_saved"] = {
                "lookback": _prefs_range.get("lookback", 250),
                "sm_n": _prefs_range.get("sm_n", 10),
                "sharpe_n": _prefs_range.get("sharpe_n", 5),
            }
        _rv = st.session_state["range_saved"]
        lookback = st.slider("历史天数 根K线", min_value=30, max_value=700, value=_rv["lookback"], step=10)
        sm_n = st.slider("突变检测窗口 (天)", min_value=5, max_value=60, value=_rv["sm_n"], step=5)
        sharpe_n = st.slider("夏普比率窗口 (天)", min_value=5, max_value=252, value=_rv["sharpe_n"], step=5)
        if st.button("保存参数", use_container_width=True, key="save_range"):
            _range_vals = {"lookback": lookback, "sm_n": sm_n, "sharpe_n": sharpe_n}
            st.session_state["range_saved"] = _range_vals
            _save_user_prefs("range", _range_vals)
            st.success("参数已保存")

        st.divider()
        with st.expander("支撑阻力位参数", expanded=False):
            # Initialize saved params from yaml defaults (once per session)
            if "sr_saved" not in st.session_state:
                _prefs_sr = _load_user_prefs().get("sr", {})
                st.session_state["sr_saved"] = {
                    "swing_order": int(_prefs_sr.get("swing_order", _sr_defaults.get("swing_order", 3))),
                    "cluster_pct": float(_prefs_sr.get("cluster_pct", _sr_defaults.get("cluster_pct", 1.5))),
                    "min_touches": int(_prefs_sr.get("min_touches", _sr_defaults.get("min_touches", 2))),
                    "min_span_days": int(_prefs_sr.get("min_span_days", _sr_defaults.get("min_span_days", 10))),
                    "max_levels": int(_prefs_sr.get("max_levels", _sr_defaults.get("max_levels", 5))),
                    "max_dist_pct": float(_prefs_sr.get("max_dist_pct", _sr_defaults.get("max_dist_pct", 15.0))),
                    "extreme_order": int(_prefs_sr.get("extreme_order", _sr_defaults.get("extreme_order", 60))),
                    "extreme_pct": float(_prefs_sr.get("extreme_pct", _sr_defaults.get("extreme_pct", 5.0))),
                    "lookback_window": int(_prefs_sr.get("lookback_window", _sr_defaults.get("lookback_window", 500))),
                }
            _sv = st.session_state["sr_saved"]

            sr_swing_order = st.slider(
                "摆动点灵敏度 (swing_order)",
                min_value=1, max_value=50,
                value=_sv["swing_order"],
                help="极值点左右各需要 N 根 K 线配合，越大越不灵敏但越可靠",
            )
            sr_cluster_pct = st.slider(
                "聚类宽松度 % (cluster_pct)",
                min_value=0.5, max_value=5.0, step=0.5,
                value=_sv["cluster_pct"],
                help="两个极值价格差 ≤ N% 归为同一水平区，越大区域越宽",
            )
            sr_min_touches = st.slider(
                "最少触及次数 (min_touches)",
                min_value=2, max_value=8,
                value=_sv["min_touches"],
                help="至少被触及几次才显示，越大越严格",
            )
            sr_min_span = st.slider(
                "最短时间跨度 天 (min_span_days)",
                min_value=5, max_value=90, step=5,
                value=_sv["min_span_days"],
                help="首次与末次触及至少间隔多少天，过滤短期密集震荡",
            )
            sr_max_levels = st.slider(
                "最多显示条数 (max_levels)",
                min_value=1, max_value=10,
                value=_sv["max_levels"],
                help="图上最多画几条关键位",
            )
            sr_max_dist_pct = st.slider(
                "最远距离过滤 % (max_dist_pct)",
                min_value=5, max_value=50, step=5,
                value=int(_sv.get("max_dist_pct", 15)),
                help="距当前价格超过此百分比的关键位直接过滤，不参与排名",
            )
            st.caption("极端单点参数")
            sr_extreme_order = st.slider(
                "极端位检测窗口 (extreme_order)",
                min_value=10, max_value=120, step=10,
                value=int(_sv.get("extreme_order", 60)),
                help="左右各 N 根K线内是最高/最低，才视为极端位候选",
            )
            sr_extreme_pct = st.slider(
                "极端位偏离阈值 % (extreme_pct)",
                min_value=1.0, max_value=20.0, step=1.0,
                value=float(_sv.get("extreme_pct", 5.0)),
                help="候选点偏离周围均价 >= N% 才确认为极端位",
            )
            sr_lookback = st.slider(
                "历史回溯窗口 根K线 (lookback_window)",
                min_value=100, max_value=1500, step=50,
                value=_sv["lookback_window"],
                help="用多少根K线历史来寻找关键位（250根≈1年，500根≈2年）",
            )
            if st.button("保存参数", use_container_width=True):
                _sr_vals = {
                    "swing_order": sr_swing_order,
                    "cluster_pct": sr_cluster_pct,
                    "min_touches": sr_min_touches,
                    "min_span_days": sr_min_span,
                    "max_levels": sr_max_levels,
                    "max_dist_pct": float(sr_max_dist_pct),
                    "extreme_order": sr_extreme_order,
                    "extreme_pct": sr_extreme_pct,
                    "lookback_window": sr_lookback,
                }
                st.session_state["sr_saved"] = _sr_vals
                _save_user_prefs("sr", _sr_vals)
                st.success("参数已保存")

    end_dt = date.today()

    # Fetch OHLCV for K-line; compute indicators on-demand from full history
    ohlcv = fetch_ohlcv(symbol)

    if ohlcv.empty:
        st.error(f"未找到 {symbol} 的 OHLCV 数据。")
        return

    # Slice by bar count; derive start_dt from the actual first bar
    ohlcv_range = ohlcv.iloc[-lookback:]
    start_dt = ohlcv_range.index[0].date()

    # Subplot toggle controls — default off, compute only when selected
    toggle_cols = st.columns(4)
    show_tech = toggle_cols[0].checkbox("技术指标（价格距离）", value=False)
    show_vol = toggle_cols[1].checkbox("量比", value=False)
    show_stat = toggle_cols[2].checkbox("统计指标", value=False)
    show_sharpe = toggle_cols[3].checkbox("夏普比率", value=False)

    # Conditional computation — only triggered by selected panels
    _empty_df = pd.DataFrame(columns=["date", "indicator", "value"])

    snap_history = _empty_df
    if show_tech or show_vol or show_stat:
        all_indicator_history = _compute_indicator_history(symbol)
        if not all_indicator_history.empty:
            snap_history = all_indicator_history[
                all_indicator_history["date"] >= start_dt
            ].copy()

    sm_hist = _empty_df
    if show_tech:
        sm_hist_all = _compute_sm_history(symbol, sm_n)
        if not sm_hist_all.empty:
            sm_hist = sm_hist_all[sm_hist_all["date"] >= start_dt].copy()

    sharpe_hist = _empty_df
    if show_sharpe:
        _sharpe_rf = settings.strategies.get("sharpe", {}).get("risk_free_rate", 0.0)
        sharpe_hist_all = _compute_sharpe_history(symbol, sharpe_n, _sharpe_rf)
        if not sharpe_hist_all.empty:
            sharpe_hist = sharpe_hist_all[sharpe_hist_all["date"] >= start_dt].copy()

    signal_history = fetch_signals_for_symbol(symbol, start_dt, end_dt)

    # Determine available indicators from computed data
    available_indicators = (
        set(snap_history["indicator"].unique()) if not snap_history.empty else set()
    )
    stat_inds_avail = [i for i in _STAT_INDICATORS if i in available_indicators]
    tech_inds_avail = [i for i in _TECH_INDICATORS if i in available_indicators]
    vol_inds_avail = [i for i in _VOL_INDICATORS if i in available_indicators]
    tech_inds = tech_inds_avail if show_tech else []
    vol_inds = vol_inds_avail if show_vol else []
    stat_inds = stat_inds_avail if show_stat else []
    show_sm_on_chart = show_tech and not sm_hist.empty
    show_sharpe_on_chart = show_sharpe and not sharpe_hist.empty

    # --- Build single-figure with stacked y-axes (one shared x-axis) ---
    # This is the only reliable way to get a vertical spike line that spans
    # all panels: make_subplots creates multiple x-axes (one per row) and Plotly
    # does not propagate hover/spike between them.  With a single x-axis and
    # multiple y-axes assigned different vertical domains, spikemode="across"
    # on the x-axis draws one vertical line through the entire figure height.
    ind_panels: list[str] = []
    if tech_inds or show_sm_on_chart:
        ind_panels.append("tech")
    if vol_inds:
        ind_panels.append("vol")
    if stat_inds:
        ind_panels.append("stat")
    if show_sharpe_on_chart:
        ind_panels.append("sharpe")

    n_ind = len(ind_panels)
    _gap = 0.03
    _n_total = 1 + n_ind
    _avail = 1.0 - _gap * max(_n_total - 1, 0)
    _kline_h = 0.45 * _avail if n_ind > 0 else _avail
    _ind_h = (_avail - _kline_h) / n_ind if n_ind > 0 else 0

    # Domains (bottom, top) — kline at top, indicator panels below
    _kline_domain = (round(1.0 - _kline_h, 4), 1.0)
    _panel_domains: dict[str, tuple[float, float]] = {}
    _top = _kline_domain[0] - _gap
    for _pname in ind_panels:
        _bot = round(_top - _ind_h, 4)
        _panel_domains[_pname] = (_bot, round(_top, 4))
        _top = _bot - _gap

    # yaxis numbers: kline → "y", panels → "y2", "y3", "y4" in order
    _yaxis_for: dict[str, str] = {
        _pname: f"y{_i + 2}" for _i, _pname in enumerate(ind_panels)
    }

    _n_dates = max(
        snap_history["date"].nunique() if not snap_history.empty else 0,
        sm_hist["date"].nunique() if not sm_hist.empty else 0,
        sharpe_hist["date"].nunique() if not sharpe_hist.empty else 0,
    )
    ind_mode = "markers" if _n_dates <= 1 else "lines+markers"
    marker_size = 8 if _n_dates <= 1 else 4

    fig = go.Figure()

    # --- K-line ---
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

    # --- Support / Resistance levels on K-line ---
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
        _x1 = max(lv["swing_dates"])
        # Skip levels whose last swing point is before the chart display range
        if _x1 < _chart_x0:
            continue
        # Skip levels whose price band is entirely outside the chart price range
        if lv["zone_high"] < _price_min * 0.95 or lv["zone_low"] > _price_max * 1.05:
            continue
        _ltype = lv["level_type"]
        _col = _SR_COLORS[_ltype]
        _is_extreme = lv.get("is_extreme", False)
        # Extreme levels: extend line across full chart; regular: first to last swing point
        if _is_extreme:
            _x0 = _chart_x0
            _x1 = _chart_x1
        else:
            _x0 = max(min(lv["swing_dates"]), _chart_x0)
        if _is_extreme:
            # Extreme pivot: solid thicker line, no shaded zone
            fig.add_shape(
                type="line",
                x0=_x0, x1=_x1, xref="x",
                y0=lv["price"], y1=lv["price"], yref="y",
                line=dict(color=_col["line"], width=2, dash="solid"),
            )
            fig.add_annotation(
                x=_x1, xref="x", y=lv["price"], yref="y",
                text=f"  {lv['price']} ★",
                showarrow=False, xanchor="left",
                font=dict(size=10, color=_col["line"]),
            )
        else:
            # Regular level: shaded zone + dashed center line
            fig.add_shape(
                type="rect",
                x0=_x0, x1=_x1, xref="x",
                y0=lv["zone_low"], y1=lv["zone_high"], yref="y",
                fillcolor=_col["fill"], line_width=0,
            )
            fig.add_shape(
                type="line",
                x0=_x0, x1=_x1, xref="x",
                y0=lv["price"], y1=lv["price"], yref="y",
                line=dict(color=_col["line"], width=1.5, dash="dash"),
            )
            fig.add_annotation(
                x=_x1, xref="x", y=lv["price"], yref="y",
                text=f"  {lv['price']} ×{lv['touches']}",
                showarrow=False, xanchor="left",
                font=dict(size=10, color=_col["line"]),
            )
        # Swing point markers: star-triangle for extreme, diamond for regular
        _sdates = [d for d in lv["swing_dates"] if d in ohlcv_range.index]
        _sprices = [
            lv["swing_prices"][lv["swing_dates"].index(d)]
            for d in _sdates
        ]
        if _sdates:
            fig.add_trace(go.Scatter(
                x=_sdates,
                y=_sprices,
                mode="markers",
                marker=dict(
                    symbol="star-triangle-up" if _is_extreme else "diamond",
                    size=10 if _is_extreme else 8,
                    color=_col["line"],
                    opacity=0.9 if _is_extreme else 0.85,
                    line=dict(width=1, color="white"),
                ),
                showlegend=False,
                hovertemplate=f"{'极端位' if _is_extreme else 'SR'} {lv['price']}<br>%{{x|%Y-%m-%d}}<br>%{{y:.2f}}<extra></extra>",
                yaxis="y",
            ))

    # Filtered-out swing points: gray circles for swings not in any SR level
    _all_swing_dates, _all_swing_prices = _compute_raw_swings(symbol, sr_swing_order, sr_lookback)
    # Collect dates already shown as diamonds (adopted by SR levels)
    _adopted_dates: set = set()
    for lv in sr_levels:
        _adopted_dates.update(lv["swing_dates"])
    # Keep only swings within chart display range and not already adopted
    _rej_dates = []
    _rej_prices = []
    for _d, _p in zip(_all_swing_dates, _all_swing_prices):
        if _d not in _adopted_dates and _d in ohlcv_range.index:
            _rej_dates.append(_d)
            _rej_prices.append(_p)
    if _rej_dates:
        fig.add_trace(go.Scatter(
            x=_rej_dates,
            y=_rej_prices,
            mode="markers",
            marker=dict(
                symbol="circle",
                size=5,
                color="#90A4AE",        # 蓝灰色，对红绿色弱友好
                opacity=0.55,
                line=dict(width=0),
            ),
            name="未采用摆动点",
            showlegend=False,
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}<extra>摆动点(未采用)</extra>",
            yaxis="y",
        ))

    # Signal markers on K-line
    if not signal_history.empty:
        seen_strategies: set[str] = set()
        for _, sig in signal_history.iterrows():
            sig_date = pd.Timestamp(sig["date"])
            if sig_date not in ohlcv_range.index:
                continue
            price = float(ohlcv_range.loc[sig_date, "close"])
            is_bull = sig["signal_type"] == "bullish"
            color = "#26a69a" if is_bull else "#ef5350"
            marker_sym = "triangle-up" if is_bull else "triangle-down"
            offset = 0.96 if is_bull else 1.04
            strat_key = f"{sig['strategy']}_{sig['signal_type']}"
            fig.add_trace(go.Scatter(
                x=[sig_date],
                y=[price * offset],
                mode="markers",
                marker=dict(symbol=marker_sym, size=14, color=color),
                name=f"{sig['strategy']} ({sig['signal_type']})",
                showlegend=strat_key not in seen_strategies,
                hovertext=f"{sig['strategy']}: {sig['signal_type']}",
                yaxis="y",
            ))
            seen_strategies.add(strat_key)

    # --- Technical indicators panel ---
    if tech_inds and not snap_history.empty:
        _yr = _yaxis_for["tech"]
        tech_df = snap_history[snap_history["indicator"].isin(tech_inds)]
        tech_pivot = tech_df.pivot(index="date", columns="indicator", values="value")
        for ind in tech_inds:
            if ind in tech_pivot.columns:
                fig.add_trace(go.Scatter(
                    x=tech_pivot.index, y=tech_pivot[ind],
                    name=_INDICATOR_LABELS.get(ind, ind),
                    mode=ind_mode, marker=dict(size=marker_size),
                    yaxis=_yr,
                ))

    if show_sm_on_chart and not sm_hist.empty:
        _yr = _yaxis_for["tech"]
        up_data = sm_hist[sm_hist["indicator"] == f"sm_up_{sm_n}d"]
        dn_data = sm_hist[sm_hist["indicator"] == f"sm_dn_{sm_n}d"]
        if not up_data.empty:
            fig.add_trace(go.Scatter(
                x=up_data["date"], y=up_data["value"],
                name=f"突变↑({sm_n}d)",
                mode=ind_mode, marker=dict(size=marker_size),
                line=dict(color="#26a69a"),
                yaxis=_yr,
            ))
        if not dn_data.empty:
            fig.add_trace(go.Scatter(
                x=dn_data["date"], y=dn_data["value"],
                name=f"突变↓({sm_n}d)",
                mode=ind_mode, marker=dict(size=marker_size),
                line=dict(color="#ef5350"),
                yaxis=_yr,
            ))
        fig.add_shape(
            type="line", x0=0, x1=1, xref="paper",
            y0=0, y1=0, yref=_yr,
            line=dict(dash="dash", color="rgba(150,150,150,0.4)", width=1),
        )

    # --- Vol ratio panel ---
    if vol_inds and not snap_history.empty:
        _yr = _yaxis_for["vol"]
        vol_df = snap_history[snap_history["indicator"].isin(vol_inds)]
        vol_pivot = vol_df.pivot(index="date", columns="indicator", values="value")
        for ind in vol_inds:
            if ind in vol_pivot.columns:
                fig.add_trace(go.Scatter(
                    x=vol_pivot.index, y=vol_pivot[ind],
                    name=_INDICATOR_LABELS.get(ind, ind),
                    mode=ind_mode, marker=dict(size=marker_size),
                    line=dict(color="#ffa726"),
                    yaxis=_yr,
                ))
        fig.add_shape(
            type="line", x0=0, x1=1, xref="paper",
            y0=1.0, y1=1.0, yref=_yr,
            line=dict(dash="dash", color="rgba(150,150,150,0.5)", width=1),
        )
        fig.add_annotation(
            x=0.99, xref="paper", y=1.0, yref=_yr,
            text="量比=1", showarrow=False, xanchor="right",
            font=dict(size=10, color="rgba(180,180,180,0.8)"),
        )

    # --- Statistical indicators panel ---
    if stat_inds and not snap_history.empty:
        _yr = _yaxis_for["stat"]
        stat_df = snap_history[snap_history["indicator"].isin(stat_inds)]
        stat_pivot = stat_df.pivot(index="date", columns="indicator", values="value")
        for ind in stat_inds:
            if ind in stat_pivot.columns:
                fig.add_trace(go.Scatter(
                    x=stat_pivot.index, y=stat_pivot[ind],
                    name=_INDICATOR_LABELS.get(ind, ind),
                    mode=ind_mode, marker=dict(size=marker_size),
                    yaxis=_yr,
                ))
        fig.add_shape(
            type="line", x0=0, x1=1, xref="paper",
            y0=0.5, y1=0.5, yref=_yr,
            line=dict(dash="dash", color="rgba(150,150,150,0.5)", width=1),
        )
        fig.add_annotation(
            x=0.99, xref="paper", y=0.5, yref=_yr,
            text="H=0.5", showarrow=False, xanchor="right",
            font=dict(size=10, color="rgba(180,180,180,0.8)"),
        )
        fig.add_shape(
            type="line", x0=0, x1=1, xref="paper",
            y0=1.0, y1=1.0, yref=_yr,
            line=dict(dash="dash", color="rgba(150,150,150,0.3)", width=1),
        )
        fig.add_annotation(
            x=0.99, xref="paper", y=1.0, yref=_yr,
            text="VR=1.0", showarrow=False, xanchor="right",
            font=dict(size=10, color="rgba(180,180,180,0.8)"),
        )

    # --- Sharpe ratio panel ---
    if show_sharpe_on_chart and not sharpe_hist.empty:
        _yr = _yaxis_for["sharpe"]
        fig.add_trace(go.Scatter(
            x=sharpe_hist["date"], y=sharpe_hist["value"],
            name=f"夏普比率({sharpe_n}d)",
            mode=ind_mode, marker=dict(size=marker_size),
            line=dict(color="#ffb74d"),
            yaxis=_yr,
        ))
        for _ref_y, _ref_label in [(0, "0"), (1.0, "1.0"), (-1.0, "-1.0")]:
            fig.add_shape(
                type="line", x0=0, x1=1, xref="paper",
                y0=_ref_y, y1=_ref_y, yref=_yr,
                line=dict(dash="dash", color="rgba(150,150,150,0.4)", width=1),
            )
            fig.add_annotation(
                x=0.99, xref="paper", y=_ref_y, yref=_yr,
                text=_ref_label, showarrow=False, xanchor="right",
                font=dict(size=10, color="rgba(180,180,180,0.8)"),
            )

    # Panel title annotations (paper-coordinate y = top of each domain)
    _panel_title_map = {
        "kline": f"{symbol} K线",
        "tech": "技术指标（价格距离）",
        "vol": "量比",
        "stat": "统计指标",
        "sharpe": f"夏普比率（{sharpe_n}d）",
    }
    for _pname, _dom in [("kline", _kline_domain)] + [
        (p, _panel_domains[p]) for p in ind_panels
    ]:
        fig.add_annotation(
            x=0.5, xref="paper", y=_dom[1], yref="paper",
            text=_panel_title_map[_pname],
            showarrow=False, xanchor="center", yanchor="bottom",
            font=dict(size=12), yshift=4,
        )

    # Build layout: single xaxis + stacked yaxes
    _spike_y = dict(
        showspikes=True,
        spikecolor="rgba(180,180,180,0.4)",
        spikethickness=1,
        spikedash="dot",
        spikesnap="cursor",
        autorange=True,
    )
    layout_kwargs: dict = {
        "height": 180 + _n_total * 230,
        "hovermode": "x unified",
        "template": "plotly_dark",
        "legend": dict(
            orientation="v", xanchor="right", x=-0.04, yanchor="top", y=1.0,
        ),
        "margin": dict(l=140, r=50, t=80, b=40),
        "xaxis": dict(
            domain=[0, 1],
            showspikes=True,
            spikemode="across",
            spikecolor="rgba(180,180,180,0.6)",
            spikethickness=1,
            spikedash="dot",
            spikesnap="cursor",
            rangeslider=dict(visible=False),
        ),
        "yaxis": dict(domain=list(_kline_domain), **_spike_y),
    }
    for _i, _pname in enumerate(ind_panels):
        layout_kwargs[f"yaxis{_i + 2}"] = dict(
            domain=list(_panel_domains[_pname]),
            anchor="x",
            **_spike_y,
        )
    fig.update_layout(**layout_kwargs)
    st.plotly_chart(fig, use_container_width=True)

    # Latest snapshot summary
    if not snap_history.empty:
        latest_date = snap_history["date"].max()
        latest = snap_history[snap_history["date"] == latest_date].copy()
        latest["指标名称"] = latest["indicator"].map(lambda x: _INDICATOR_LABELS.get(x, x))
        date_label = latest_date.date() if hasattr(latest_date, "date") else latest_date
        st.subheader(f"最新指标值（{date_label}）")
        st.dataframe(
            latest[["指标名称", "value"]].rename(columns={"value": "数值"}),
            use_container_width=True,
            hide_index=True,
        )

    # Recent signals
    if not signal_history.empty:
        st.subheader("历史信号记录")
        sig_disp = signal_history[["date", "strategy", "signal_type", "detail_json"]].copy()
        sig_disp["signal_type"] = sig_disp["signal_type"].map(
            lambda x: f"{_SIGNAL_EMOJI.get(x, '')} {x}"
        )
        st.dataframe(sig_disp, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# DB Viewer page
# ---------------------------------------------------------------------------
def show_db_viewer() -> None:
    st.title("🗄️ 数据库查看器")

    with st.sidebar:
        st.header("数据库")
        db_choice = st.radio(
            "选择数据库",
            options=["market_analysis", "market_data"],
            index=0,
        )
        use_source = db_choice == "market_data"
        st.divider()
        if st.button("🔄 刷新"):
            st.rerun()

    # Auto-discover tables
    try:
        tables = fetch_db_table_names(use_source=use_source)
    except Exception as e:
        st.error(f"无法连接数据库：{e}")
        return

    if not tables:
        st.info("该数据库没有任何表。")
        return

    selected_table = st.selectbox("选择表", options=tables)

    # Table metadata
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

    # Pagination
    page_size = st.select_slider("每页行数", options=[20, 50, 100, 200, 500], value=100)
    total_pages = max(1, (row_count + page_size - 1) // page_size)
    page_num = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1)
    offset = (page_num - 1) * page_size
    st.caption(f"第 {page_num}/{total_pages} 页，共 {row_count:,} 行")

    try:
        df = fetch_db_table_data(
            selected_table, limit=page_size, offset=offset, use_source=use_source
        )
    except Exception as e:
        st.error(f"读取数据失败：{e}")
        return

    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Global navigation (always visible in sidebar)
# ---------------------------------------------------------------------------
_NAV_LABELS = {
    "overview": "📈 信号总览",
    "db_viewer": "🗄️ 数据库查看器",
}

# Inject nav at top of sidebar (outside page functions)
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
        # Coming back from detail page via nav
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
