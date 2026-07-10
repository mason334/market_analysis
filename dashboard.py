from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
import yaml

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_db_table_columns,
    fetch_db_table_data,
    fetch_db_table_names,
    fetch_db_table_row_count,
    fetch_indicators_daily_by_date,
    fetch_indicators_daily_filtered,
    fetch_indicators_daily_for_symbol,
    fetch_indicators_daily_symbols,
    fetch_latest_indicators_daily_date,
    fetch_latest_indicators_daily_for_symbol,
    fetch_constituent_turnover_batch,
    fetch_constituents_for_ticker,
    fetch_universe_ticker_list,
    fetch_universe_category_maps,
    fetch_latest_sector_heat_date,
    fetch_latest_sector_heat_for_ticker,
    fetch_ohlcv,
    fetch_sector_heat_snapshot,
)
from market_analysis.analytics.sector_heat import compute_sector_heat_history
from market_analysis.strategies.support_resistance import (
    compute_raw_swings,
    compute_sr_levels,
)

st.set_page_config(page_title="Market Analysis", layout="wide", page_icon="📈")

# ---------------------------------------------------------------------------
# URL query param — detail page opened in new tab
# ---------------------------------------------------------------------------
_symbol_from_url: str | None = st.query_params.get("symbol")
_sector_from_url: str | None = st.query_params.get("sector")

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
    st.session_state.selected_sector = None

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


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_etf_set() -> frozenset[str]:
    return frozenset(fetch_universe_ticker_list())


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_stock_set() -> frozenset[str]:
    return frozenset(fetch_constituents_for_ticker("OPTIONS_ACTIVE"))


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_etf_category_maps() -> tuple[dict[str, str], dict[str, str]]:
    return fetch_universe_category_maps()


_UNIVERSE_FILE = Path(__file__).parent / "config" / "universe.yaml"
_USER_PREFS_FILE = Path(__file__).parent / "config" / "user_prefs.yaml"
_PROJECT_ROOT = Path(__file__).parent


def _run_cli_command(command: str) -> subprocess.CompletedProcess[str]:
    """Run the local CLI with the same Python interpreter as Streamlit."""
    return subprocess.run(
        [sys.executable, "-m", "market_analysis.cli", command],
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        errors="replace",
        text=True,
        cwd=str(_PROJECT_ROOT),
    )


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
    "trend_slope_10d", "trend_r2_10d",
    "trend_slope_20d", "trend_r2_20d",
    "trend_slope_40d", "trend_r2_40d",
    "trend_slope_60d", "trend_r2_60d",
]




# ---------------------------------------------------------------------------
# Overview page
# ---------------------------------------------------------------------------
def show_overview() -> None:
    st.title("📈 Market Analysis — 策略快照")

    latest_db_date = fetch_latest_indicators_daily_date()
    default_date = latest_db_date if latest_db_date is not None else date.today()

    with st.sidebar:
        st.header("筛选")
        selected_date = st.date_input("日期", value=default_date)
        if latest_db_date is not None and latest_db_date < date.today():
            st.caption(f"最新数据：{latest_db_date}（今日数据待更新）")
        st.divider()
        if st.button("▶ 运行策略分析", use_container_width=True):
            with st.spinner("正在运行 market-analysis run-strategies ..."):
                result = _run_cli_command("run-strategies")
            if result.returncode == 0:
                st.success("分析完成，正在刷新数据...")
                st.cache_data.clear()
                st.rerun()
            else:
                output = result.stderr[-500:] if result.stderr else result.stdout[-500:]
                st.error(f"运行失败：\n```\n{output}\n```")

        if st.button("🔄 刷新数据", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.caption("列显示")
        show_sr       = st.checkbox("支撑 / 阻力", value=True)
        show_breakout = st.checkbox("突破信号", value=True)
        show_5d       = st.checkbox("斜率 5d", value=True)
        show_10d      = st.checkbox("斜率 10d", value=True)
        show_20d      = st.checkbox("斜率 20d", value=True)
        show_40d      = st.checkbox("斜率 40d", value=False)
        show_60d      = st.checkbox("斜率 60d", value=False)
        show_11_20d   = st.checkbox("斜率 d11-20", value=False)
        show_20_40d   = st.checkbox("斜率 d20-40", value=False)
        show_40_60d   = st.checkbox("斜率 d40-60", value=False)

    df = fetch_indicators_daily_by_date(selected_date)

    if df.empty:
        st.warning(
            f"**{selected_date}** 暂无数据。\n\n"
            "请运行以下命令生成分析并写入数据库：\n"
            "```\nmarket-analysis run-sr\n```"
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

    # Missing symbols for selected_date
    _present = set(df["symbol"].tolist())
    _etf_s = _fetch_etf_set()
    _stk_s = _fetch_stock_set()
    _missing_etf = sorted(_etf_s - _present)
    _missing_stk = sorted(_stk_s - _present)
    if _missing_etf or _missing_stk:
        _parts = []
        if _missing_etf:
            _parts.append(f"ETF 缺失 {len(_missing_etf)} 个：{', '.join(_missing_etf)}")
        if _missing_stk:
            _parts.append(f"个股缺失 {len(_missing_stk)} 个：{', '.join(_missing_stk)}")
        st.caption("当前日期数据不完整：" + "；".join(_parts))

    st.divider()

    # Build display table
    display = df.copy()
    display["_sort"] = display["sr_status"].map(lambda s: _STATUS_SORT.get(s, 99))
    display = display.sort_values(["_sort", "symbol"], ignore_index=True)
    display["状态"] = display["sr_status"].map(lambda s: _STATUS_LABEL.get(s, s))
    display["突破(5d)"] = display["breakout_5d"].map(lambda v: _BREAKOUT_LABEL.get(v, ""))
    display["斜率(5d)"] = pd.to_numeric(display["trend_slope_5d"], errors="coerce") * 100
    display["R²(5d)"] = pd.to_numeric(display["trend_r2_5d"], errors="coerce")
    display["斜率(10d)"] = pd.to_numeric(display["trend_slope_10d"], errors="coerce") * 100
    display["R²(10d)"] = pd.to_numeric(display["trend_r2_10d"], errors="coerce")
    display["斜率(20d)"] = pd.to_numeric(display["trend_slope_20d"], errors="coerce") * 100
    display["R²(20d)"] = pd.to_numeric(display["trend_r2_20d"], errors="coerce")
    display["斜率(40d)"] = pd.to_numeric(display["trend_slope_40d"], errors="coerce") * 100
    display["R²(40d)"] = pd.to_numeric(display["trend_r2_40d"], errors="coerce")
    display["斜率(60d)"] = pd.to_numeric(display["trend_slope_60d"], errors="coerce") * 100
    display["R²(60d)"] = pd.to_numeric(display["trend_r2_60d"], errors="coerce")
    display["斜率(d11-20)"] = pd.to_numeric(display["trend_slope_11_20d"], errors="coerce") * 100
    display["R²(d11-20)"] = pd.to_numeric(display["trend_r2_11_20d"], errors="coerce")
    display["斜率(d20-40)"] = pd.to_numeric(display["trend_slope_20_40d"], errors="coerce") * 100
    display["R²(d20-40)"] = pd.to_numeric(display["trend_r2_20_40d"], errors="coerce")
    display["斜率(d40-60)"] = pd.to_numeric(display["trend_slope_40_60d"], errors="coerce") * 100
    display["R²(d40-60)"] = pd.to_numeric(display["trend_r2_40_60d"], errors="coerce")
    # Keep numeric columns as float (NaN for missing) so table sorting works correctly
    display["支撑价"] = pd.to_numeric(display["nearest_support"], errors="coerce")
    display["距支撑(ATR)"] = pd.to_numeric(display["dist_support_atr"], errors="coerce")
    display["阻力价"] = pd.to_numeric(display["nearest_resistance"], errors="coerce")
    display["距阻力(ATR)"] = pd.to_numeric(display["dist_resistance_atr"], errors="coerce")

    show_cols = ["symbol", "状态"]
    if show_sr:
        show_cols += ["支撑价", "距支撑(ATR)", "阻力价", "距阻力(ATR)"]
    if show_breakout:
        show_cols += ["突破(5d)"]
    if show_5d:
        show_cols += ["斜率(5d)", "R²(5d)"]
    if show_10d:
        show_cols += ["斜率(10d)", "R²(10d)"]
    if show_20d:
        show_cols += ["斜率(20d)", "R²(20d)"]
    if show_40d:
        show_cols += ["斜率(40d)", "R²(40d)"]
    if show_60d:
        show_cols += ["斜率(60d)", "R²(60d)"]
    if show_11_20d:
        show_cols += ["斜率(d11-20)", "R²(d11-20)"]
    if show_20_40d:
        show_cols += ["斜率(d20-40)", "R²(d20-40)"]
    if show_40_60d:
        show_cols += ["斜率(d40-60)", "R²(d40-60)"]

    # Group by DB source: ETF = universe table; 个股 = OPTIONS_ACTIVE constituents
    _etf_set: frozenset[str] = _fetch_etf_set()
    _stock_set: frozenset[str] = _fetch_stock_set()
    _known = _etf_set | _stock_set

    # ETF table: add category + sub_category columns from universe table
    _cat_map, _subcat_map = _fetch_etf_category_maps()
    etf_base = display[display["symbol"].isin(_etf_set)].copy()
    etf_base["大类"] = etf_base["symbol"].map(_cat_map).fillna("")
    etf_base["类别"] = etf_base["symbol"].map(_subcat_map).fillna("")
    etf_show_cols = ["symbol", "大类", "类别"] + show_cols[1:]  # insert after symbol
    display_etf   = etf_base[etf_show_cols].copy()

    display_stock = display[display["symbol"].isin(_stock_set)][show_cols].copy()
    display_other = display[~display["symbol"].isin(_known)][show_cols].copy()

    st.subheader(f"{selected_date} 策略快照")
    st.caption("点击任意行，在新标签页查看该股票详情")

    with st.expander("状态说明", expanded=False):
        st.markdown(
            """
| 状态 | 含义 | 触发条件 |
|------|------|---------|
| 🔵 在支撑位 | 价格已进入 SR 区，从上方跌入（支撑） | 收盘在区间内，向前回溯确认从上方进入 |
| 🔴 在阻力位 | 价格已进入 SR 区，从下方涨入（阻力） | 收盘在区间内，向前回溯确认从下方进入 |
| ⚠️ 警告 | 距最近 SR 区非常近 | 距区间边缘 ≤ 0.5×ATR14 |
| 👀 关注 | 距最近 SR 区较近 | 距区间边缘 ≤ 1.5×ATR14 |
| （空） | 正常，距所有 SR 区较远 | 距所有区间边缘 > 1.5×ATR14 |

**突破(5d)**：过去 5 根 K 线内价格穿越某 SR 区中心价格，🟢 向上突破 / 🔴 向下跌破。

**距支撑/阻力(ATR)**：正值 = 尚未到达，负值 = 已在区间内。绝对值越小说明越近。
            """
        )

    _col_cfg = {
        "支撑价":      st.column_config.NumberColumn("支撑价",      format="%.2f"),
        "距支撑(ATR)": st.column_config.NumberColumn("距支撑(ATR)", format="%.2f"),
        "阻力价":      st.column_config.NumberColumn("阻力价",      format="%.2f"),
        "距阻力(ATR)": st.column_config.NumberColumn("距阻力(ATR)", format="%.2f"),
        "斜率(5d)":    st.column_config.NumberColumn("斜率(5d)",    format="%+.3f%%"),
        "R²(5d)":      st.column_config.NumberColumn("R²(5d)",      format="%.2f"),
        "斜率(10d)":   st.column_config.NumberColumn("斜率(10d)",   format="%+.3f%%"),
        "R²(10d)":     st.column_config.NumberColumn("R²(10d)",     format="%.2f"),
        "斜率(20d)":   st.column_config.NumberColumn("斜率(20d)",   format="%+.3f%%"),
        "R²(20d)":     st.column_config.NumberColumn("R²(20d)",     format="%.2f"),
        "斜率(40d)":   st.column_config.NumberColumn("斜率(40d)",   format="%+.3f%%"),
        "R²(40d)":     st.column_config.NumberColumn("R²(40d)",     format="%.2f"),
        "斜率(60d)":    st.column_config.NumberColumn("斜率(60d)",    format="%+.3f%%"),
        "R²(60d)":      st.column_config.NumberColumn("R²(60d)",      format="%.2f"),
        "斜率(d11-20)": st.column_config.NumberColumn("斜率(d11-20)", format="%+.3f%%"),
        "R²(d11-20)":   st.column_config.NumberColumn("R²(d11-20)",   format="%.2f"),
        "斜率(d20-40)": st.column_config.NumberColumn("斜率(d20-40)", format="%+.3f%%"),
        "R²(d20-40)":   st.column_config.NumberColumn("R²(d20-40)",   format="%.2f"),
        "斜率(d40-60)": st.column_config.NumberColumn("斜率(d40-60)", format="%+.3f%%"),
        "R²(d40-60)":   st.column_config.NumberColumn("R²(d40-60)",   format="%.2f"),
    }

    def _render_table(df: pd.DataFrame, key: str, max_height: int = 0) -> None:
        if df.empty:
            return
        row_height = 35
        natural = len(df) * row_height + 38
        table_height = natural if max_height == 0 else min(natural, max_height)
        event = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            height=table_height,
            on_select="rerun",
            selection_mode="single-row",
            key=key,
            column_config=_col_cfg,
        )
        if event.selection and event.selection.rows:
            row_idx = event.selection.rows[0]
            symbol = str(df.iloc[row_idx]["symbol"])
            components.html(
                f'<script>window.parent.open("/?symbol={symbol}", "_blank");</script>',
                height=0,
            )

    if not display_etf.empty:
        st.markdown(f"**板块/Theme ETF**（{len(display_etf)} 个）")
        _render_table(display_etf, key="tbl_etf", max_height=500)

    if not display_stock.empty:
        st.markdown(f"**Option Active个股**（{len(display_stock)} 个）")
        _render_table(display_stock, key="tbl_stock", max_height=600)

    if not display_other.empty:
        st.markdown(f"**其他**（{len(display_other)} 个）")
        _render_table(display_other, key="tbl_other", max_height=600)


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
        col4.metric("斜率(5d)", f"{latest['trend_slope_5d']*100:+.3f}%" if latest.get("trend_slope_5d") is not None else "—")
        col4.metric("斜率(10d)", f"{latest['trend_slope_10d']*100:+.3f}%" if latest.get("trend_slope_10d") is not None else "—")
        col4.metric("斜率(20d)", f"{latest['trend_slope_20d']*100:+.3f}%" if latest.get("trend_slope_20d") is not None else "—")
        col4.metric("斜率(40d)", f"{latest['trend_slope_40d']*100:+.3f}%" if latest.get("trend_slope_40d") is not None else "—")
        col4.metric("斜率(60d)", f"{latest['trend_slope_60d']*100:+.3f}%" if latest.get("trend_slope_60d") is not None else "—")

    # Historical snapshot table
    st.subheader("历史快照")
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=90)
    hist = fetch_indicators_daily_for_symbol(symbol, start_dt, end_dt)
    if not hist.empty:
        hist_disp = hist[["date", "sr_status", "nearest_support", "nearest_resistance",
                           "dist_support_atr", "dist_resistance_atr",
                           "breakout_5d", "trend_slope_5d", "trend_r2_5d",
                           "trend_slope_10d", "trend_r2_10d",
                           "trend_slope_20d", "trend_r2_20d",
                           "trend_slope_40d", "trend_r2_40d",
                           "trend_slope_60d", "trend_r2_60d"]].copy()
        hist_disp["sr_status"] = hist_disp["sr_status"].map(lambda s: _STATUS_LABEL.get(s, s))
        hist_disp["breakout_5d"] = hist_disp["breakout_5d"].map(lambda v: _BREAKOUT_LABEL.get(v, ""))
        for col in ["trend_slope_5d", "trend_slope_10d", "trend_slope_20d",
                    "trend_slope_40d", "trend_slope_60d"]:
            hist_disp[col] = hist_disp[col].map(lambda v: f"{v*100:+.3f}%" if pd.notna(v) else "")
        hist_disp = hist_disp.rename(columns={
            "date": "日期", "sr_status": "状态",
            "nearest_support": "支撑价", "nearest_resistance": "阻力价",
            "dist_support_atr": "距支撑(ATR)", "dist_resistance_atr": "距阻力(ATR)",
            "breakout_5d": "突破(5d)",
            "trend_slope_5d": "斜率(5d)", "trend_r2_5d": "R²(5d)",
            "trend_slope_10d": "斜率(10d)", "trend_r2_10d": "R²(10d)",
            "trend_slope_20d": "斜率(20d)", "trend_r2_20d": "R²(20d)",
            "trend_slope_40d": "斜率(40d)", "trend_r2_40d": "R²(40d)",
            "trend_slope_60d": "斜率(60d)", "trend_r2_60d": "R²(60d)",
        })
        st.dataframe(hist_disp, use_container_width=True, hide_index=True)
    else:
        st.info("暂无历史快照数据。")


# ---------------------------------------------------------------------------
# Sector Heat helpers
# ---------------------------------------------------------------------------

def _heat_color(ratio: float | None) -> str:
    """Return a color string for a given turnover_ratio."""
    if ratio is None:
        return ""
    if ratio >= 3.0:
        return "🔴"
    if ratio >= 2.0:
        return "🟠"
    if ratio >= 1.5:
        return "🟡"
    return ""


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_sector_heat_snapshot_cached(target_date: "date") -> pd.DataFrame:
    return fetch_sector_heat_snapshot(target_date)


@st.cache_data(ttl=600, show_spinner="正在计算板块历史热度...")
def _compute_sector_heat_history_cached(
    universe_ticker: str,
    start_dt: "date",
    end_dt: "date",
    source: str = "tiingo",
) -> pd.DataFrame:
    """
    Fetch raw constituent turnover and compute rolling heat metrics dynamically.
    Adds 90-day warm-up before start_dt so MA20 / z-score are valid from day 1.
    Returns data filtered to [start_dt, end_dt].
    """
    stock_tickers = fetch_constituents_for_ticker(universe_ticker)
    if not stock_tickers:
        return pd.DataFrame()
    warmup_start = start_dt - timedelta(days=90)
    wide_df = fetch_constituent_turnover_batch(stock_tickers, warmup_start, source=source)
    if wide_df.empty:
        return pd.DataFrame()
    hist_df = compute_sector_heat_history(universe_ticker, wide_df)
    if hist_df.empty:
        return pd.DataFrame()
    # Filter to the user-requested display range
    mask = (hist_df["date"] >= pd.Timestamp(start_dt)) & (hist_df["date"] <= pd.Timestamp(end_dt))
    return hist_df[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Sector Heat Overview page
# ---------------------------------------------------------------------------

def show_sector_heat_overview() -> None:
    st.title("🔥 板块资金热度")

    latest_db_date = fetch_latest_sector_heat_date()
    default_date = latest_db_date if latest_db_date is not None else date.today()

    with st.sidebar:
        st.header("筛选")
        selected_date = st.date_input("日期", value=default_date, key="sh_date")
        if latest_db_date is not None and latest_db_date < date.today():
            st.caption(f"最新数据：{latest_db_date}（今日数据待更新）")
        st.divider()
        if st.button("▶ 运行热度分析", use_container_width=True):
            with st.spinner("正在运行 market-analysis run-sector-heat ..."):
                result = _run_cli_command("run-sector-heat")
            if result.returncode == 0:
                st.success("分析完成，正在刷新数据...")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(
                    f"运行失败：\n```\n"
                    f"{result.stderr[-500:] if result.stderr else result.stdout[-500:]}\n```"
                )
        if st.button("🔄 刷新数据", use_container_width=True, key="sh_refresh"):
            st.cache_data.clear()
            st.rerun()

    df = _fetch_sector_heat_snapshot_cached(selected_date)

    if df.empty:
        st.warning(
            f"**{selected_date}** 暂无板块热度数据。\n\n"
            "请运行：\n```\nmarket-analysis run-sector-heat\n```"
        )
        return

    # Metrics bar
    n_total = len(df)
    n_hot = int((pd.to_numeric(df["turnover_ratio"], errors="coerce") >= 2.0).sum())
    n_very_hot = int((pd.to_numeric(df["turnover_ratio"], errors="coerce") >= 3.0).sum())
    avg_ratio = pd.to_numeric(df["turnover_ratio"], errors="coerce").mean()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("板块总数", n_total)
    c2.metric("放量板块 (ratio≥2)", n_hot)
    c3.metric("异常放量 (ratio≥3)", n_very_hot)
    c4.metric("平均热度倍数", f"{avg_ratio:.2f}" if pd.notna(avg_ratio) else "—")

    st.divider()
    st.subheader(f"{selected_date} 板块热度快照")
    st.caption("点击任意行，在新标签页查看该板块详情")

    with st.expander("指标说明", expanded=False):
        st.markdown(
            """
| 指标 | 含义 |
|------|------|
| 成交额 | 板块成分股 `close × volume` 之和（美元） |
| 热度倍数 | 今日成交额 / 20日均值，>2 放量，>3 异常放量 |
| Z-score | 60日统计偏差，>2σ 为统计意义上的异常 |
| 成分股数 | 当天有行情数据的成分股数量 |

**热度标记**：🔴 ratio≥3（异常放量）　🟠 ratio≥2（明显放量）　🟡 ratio≥1.5（温和放量）
            """
        )

    display = df.copy()
    display["热度"] = pd.to_numeric(display["turnover_ratio"], errors="coerce").map(
        lambda v: _heat_color(v) if pd.notna(v) else ""
    )
    display["成交额(亿$)"] = pd.to_numeric(display["sector_turnover"], errors="coerce") / 1e8
    display["均值(亿$)"] = pd.to_numeric(display["turnover_ma20"], errors="coerce") / 1e8
    display["热度倍数"] = pd.to_numeric(display["turnover_ratio"], errors="coerce")
    display["Z-score"] = pd.to_numeric(display["turnover_zscore"], errors="coerce")
    display["成分股"] = display["constituent_count"]

    show_cols = ["universe_ticker", "热度", "成交额(亿$)", "均值(亿$)", "热度倍数", "Z-score", "成分股"]

    col_cfg = {
        "成交额(亿$)": st.column_config.NumberColumn("成交额(亿$)", format="%.2f"),
        "均值(亿$)":   st.column_config.NumberColumn("均值(亿$)",   format="%.2f"),
        "热度倍数":    st.column_config.NumberColumn("热度倍数",    format="%.2f"),
        "Z-score":     st.column_config.NumberColumn("Z-score",     format="%.2f"),
    }

    row_height = 35
    table_height = min(len(display) * row_height + 38, 600)
    event = st.dataframe(
        display[show_cols],
        use_container_width=True,
        hide_index=True,
        height=table_height,
        on_select="rerun",
        selection_mode="single-row",
        key="sh_overview_tbl",
        column_config=col_cfg,
    )
    if event.selection and event.selection.rows:
        row_idx = event.selection.rows[0]
        sector = str(display[show_cols].iloc[row_idx]["universe_ticker"])
        components.html(
            f'<script>window.parent.open("/?sector={sector}", "_blank");</script>',
            height=0,
        )


# ---------------------------------------------------------------------------
# Sector Heat Detail page
# ---------------------------------------------------------------------------

# Row heights in px used to compute total figure height
_SECTOR_ROW_HEIGHT_PX: dict[str, int] = {
    "kline": 340, "turnover": 190, "ratio": 190, "zscore": 190,
}
# Relative weights for y-axis domain sizing
_SECTOR_ROW_WEIGHT: dict[str, float] = {
    "kline": 3.4, "turnover": 1.9, "ratio": 1.9, "zscore": 1.9,
}


def show_sector_heat_detail(universe_ticker: str) -> None:
    if st.button("← 返回板块热度"):
        st.session_state.page = "sector_heat"
        st.session_state.selected_sector = None
        st.rerun()

    st.title(f"🔥 {universe_ticker} — 板块热度详情")

    latest = fetch_latest_sector_heat_for_ticker(universe_ticker)
    if latest:
        ratio = latest.get("turnover_ratio")
        snap_date = latest.get("date")
        if hasattr(snap_date, "date"):
            snap_date = snap_date.date()
        if ratio is not None and ratio >= 3.0:
            st.error(f"🔴 **异常放量** — 热度倍数 {ratio:.2f}x（数据日期：{snap_date}）")
        elif ratio is not None and ratio >= 2.0:
            st.warning(f"🟠 **明显放量** — 热度倍数 {ratio:.2f}x（数据日期：{snap_date}）")
        elif ratio is not None and ratio >= 1.5:
            st.info(f"🟡 **温和放量** — 热度倍数 {ratio:.2f}x（数据日期：{snap_date}）")

    # First sidebar block: date range
    with st.sidebar:
        st.header("历史范围")
        hist_days = st.slider("历史天数", min_value=60, max_value=500, value=180, step=30)
        st.divider()

    end_dt = date.today()
    start_dt = end_dt - timedelta(days=hist_days)

    # Check ETF K-line availability (cached, near-instant)
    ohlcv = _fetch_ohlcv_cached(universe_ticker)
    ohlcv_range = ohlcv[ohlcv.index >= pd.Timestamp(start_dt)] if not ohlcv.empty else pd.DataFrame()
    has_kline = not ohlcv_range.empty

    # Second sidebar block: indicator toggles (needs has_kline)
    with st.sidebar:
        st.subheader("指标开关")
        show_kline    = st.checkbox("K线图",          value=has_kline,  disabled=not has_kline,
                                    help="无ETF行情数据" if not has_kline else None)
        show_turnover = st.checkbox("成交额 + MA20",   value=True)
        show_ratio    = st.checkbox("热度倍数 (ratio)", value=True)
        show_zscore   = st.checkbox("Z-score (60日)",  value=True)
        st.divider()

    # Fetch dynamic heat history
    hist_df = _compute_sector_heat_history_cached(universe_ticker, start_dt, end_dt)
    if hist_df.empty:
        st.info("暂无成分股行情数据，请确认 universe_constituents 表已有该板块的成分股。")
        return

    # Build subplot row list based on toggles
    row_labels: list[str] = []
    if show_kline and has_kline:
        row_labels.append("kline")
    if show_turnover:
        row_labels.append("turnover")
    if show_ratio:
        row_labels.append("ratio")
    if show_zscore:
        row_labels.append("zscore")

    if not row_labels:
        st.info("请至少开启一个指标。")
        return

    n_rows = len(row_labels)
    total_height = sum(_SECTOR_ROW_HEIGHT_PX[l] for l in row_labels)
    weights = [_SECTOR_ROW_WEIGHT[l] for l in row_labels]
    total_weight = sum(weights)

    # Build Plotly y-axis domains (0=bottom, 1=top).
    # row_labels[0] is the topmost panel; row_labels[-1] is the bottom.
    # We build from the bottom upward.
    _spacing = 0.025
    _avail = 1.0 - _spacing * max(n_rows - 1, 0)
    _domains: dict[str, list[float]] = {}
    _cursor = 0.0
    for _lbl, _w in zip(reversed(row_labels), reversed(weights)):
        _frac = _w / total_weight * _avail
        _domains[_lbl] = [round(_cursor, 5), round(_cursor + _frac, 5)]
        _cursor += _frac + _spacing

    # y-axis key helpers: row 0 → "y"/"yaxis", row 1 → "y2"/"yaxis2", …
    def _yref(i: int) -> str:
        return "y" if i == 0 else f"y{i + 1}"

    def _ykey(i: int) -> str:
        return "yaxis" if i == 0 else f"yaxis{i + 1}"

    _spike_y = dict(
        showspikes=True,
        spikemode="across",
        spikecolor="rgba(180,180,180,0.4)",
        spikethickness=1,
        spikedash="dot",
        spikesnap="cursor",
    )

    x_min = hist_df["date"].iloc[0]
    x_max = hist_df["date"].iloc[-1]

    # Single x-axis: all traces share xaxis="x", so one spike covers all rows.
    fig = go.Figure()

    layout_extra: dict = {
        "xaxis": dict(
            showspikes=True,
            spikemode="across",
            spikecolor="rgba(180,180,180,0.6)",
            spikethickness=1,
            spikedash="dot",
            spikesnap="cursor",
            rangeslider=dict(visible=False),
            domain=[0.0, 1.0],
        ),
    }

    for row_idx, label in enumerate(row_labels):
        yr   = _yref(row_idx)
        ykey = _ykey(row_idx)
        dom  = _domains[label]

        if label == "kline":
            fig.add_trace(go.Candlestick(
                x=ohlcv_range.index,
                open=ohlcv_range["open"], high=ohlcv_range["high"],
                low=ohlcv_range["low"],  close=ohlcv_range["close"],
                name="K线",
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
                showlegend=False,
                yaxis=yr,
            ))
            layout_extra[ykey] = dict(domain=dom, anchor="x", title_text="价格", **_spike_y)

        elif label == "turnover":
            fig.add_trace(go.Bar(
                x=hist_df["date"],
                y=hist_df["sector_turnover"] / 1e8,
                name="日成交额(亿$)",
                marker_color="rgba(33,150,243,0.5)",
                yaxis=yr,
            ))
            if hist_df["turnover_ma20"].notna().any():
                fig.add_trace(go.Scatter(
                    x=hist_df["date"],
                    y=hist_df["turnover_ma20"] / 1e8,
                    name="MA20(亿$)",
                    line=dict(color="#ffd600", width=2),
                    yaxis=yr,
                ))
            layout_extra[ykey] = dict(domain=dom, anchor="x", title_text="成交额(亿$)", **_spike_y)

        elif label == "ratio":
            valid = hist_df["turnover_ratio"].notna()
            fig.add_trace(go.Scatter(
                x=hist_df.loc[valid, "date"],
                y=hist_df.loc[valid, "turnover_ratio"],
                name="热度倍数",
                line=dict(color="#26a69a", width=2),
                yaxis=yr,
            ))
            for level, color, lname in [
                (1.0, "rgba(180,180,180,0.35)", "基准(1x)"),
                (2.0, "rgba(255,193,7,0.55)",   "放量(2x)"),
                (3.0, "rgba(239,83,80,0.55)",   "异常(3x)"),
            ]:
                fig.add_trace(go.Scatter(
                    x=[x_min, x_max], y=[level, level],
                    mode="lines", name=lname,
                    line=dict(color=color, width=1, dash="dash"),
                    showlegend=True, hoverinfo="skip",
                    yaxis=yr,
                ))
            layout_extra[ykey] = dict(domain=dom, anchor="x", title_text="热度倍数(x)", **_spike_y)

        elif label == "zscore":
            valid = hist_df["turnover_zscore"].notna()
            fig.add_trace(go.Scatter(
                x=hist_df.loc[valid, "date"],
                y=hist_df.loc[valid, "turnover_zscore"],
                name="Z-score",
                line=dict(color="#ab47bc", width=2),
                yaxis=yr,
            ))
            for level, color in [(2.0, "rgba(255,193,7,0.45)"), (-2.0, "rgba(255,193,7,0.45)")]:
                fig.add_trace(go.Scatter(
                    x=[x_min, x_max], y=[level, level],
                    mode="lines",
                    line=dict(color=color, width=1, dash="dash"),
                    name=f"±2σ ({level:+.0f})",
                    showlegend=False, hoverinfo="skip",
                    yaxis=yr,
                ))
            layout_extra[ykey] = dict(domain=dom, anchor="x", title_text="Z-score(σ)", **_spike_y)

    fig.update_layout(
        **layout_extra,
        height=total_height,
        template="plotly_dark",
        hovermode="x",
        margin=dict(l=60, r=60, t=30, b=40),
        legend=dict(orientation="h", x=0, y=1.02, xanchor="left", yanchor="bottom"),
        barmode="overlay",
    )
    st.plotly_chart(fig, use_container_width=True)

    # Latest snapshot metrics (from DB pre-computed row)
    if latest:
        st.subheader("最新快照")
        m1, m2, m3, m4 = st.columns(4)
        turnover_b = latest.get("sector_turnover")
        ma20_b = latest.get("turnover_ma20")
        m1.metric("成交额(亿$)", f"{turnover_b/1e8:.2f}" if turnover_b else "—")
        m2.metric("MA20(亿$)", f"{ma20_b/1e8:.2f}" if ma20_b else "—")
        m3.metric("热度倍数", f"{latest.get('turnover_ratio'):.2f}" if latest.get("turnover_ratio") else "—")
        m4.metric("Z-score", f"{latest.get('turnover_zscore'):.2f}" if latest.get("turnover_zscore") is not None else "—")

    # Historical data table
    st.subheader("历史数据")
    tbl = hist_df.sort_values("date", ascending=False).copy()
    tbl["成交额(亿$)"] = tbl["sector_turnover"] / 1e8
    tbl["均值(亿$)"]   = tbl["turnover_ma20"] / 1e8
    tbl["热度倍数"]    = tbl["turnover_ratio"]
    tbl["Z-score"]     = tbl["turnover_zscore"]
    tbl["成分股"]      = tbl["constituent_count"]
    tbl["日期"]        = tbl["date"].dt.date
    st.dataframe(
        tbl[["日期", "成交额(亿$)", "均值(亿$)", "热度倍数", "Z-score", "成分股"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "成交额(亿$)": st.column_config.NumberColumn(format="%.2f"),
            "均值(亿$)":   st.column_config.NumberColumn(format="%.2f"),
            "热度倍数":    st.column_config.NumberColumn(format="%.2f"),
            "Z-score":     st.column_config.NumberColumn(format="%.2f"),
        },
    )


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

    # --- indicators_daily: symbol + date filter ---
    if not use_source and selected_table == "indicators_daily":
        try:
            all_symbols = fetch_indicators_daily_symbols()
        except Exception:
            all_symbols = []

        fc1, fc2, fc3 = st.columns([2, 1, 1])
        sel_symbols = fc1.multiselect("Symbol 筛选", options=all_symbols, placeholder="全部")
        min_date = fc2.date_input("开始日期", value=None, key="dbv_date_from")
        max_date = fc3.date_input("结束日期", value=None, key="dbv_date_to")

        page_size = st.select_slider("每页行数", options=[20, 50, 100, 200, 500], value=100)

        try:
            df, total = fetch_indicators_daily_filtered(
                symbols=sel_symbols or None,
                date_from=min_date or None,
                date_to=max_date or None,
                limit=page_size,
                offset=0,
            )
            total_pages = max(1, (total + page_size - 1) // page_size)
            page_num = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1)
            if page_num > 1:
                df, total = fetch_indicators_daily_filtered(
                    symbols=sel_symbols or None,
                    date_from=min_date or None,
                    date_to=max_date or None,
                    limit=page_size,
                    offset=(page_num - 1) * page_size,
                )
            st.caption(f"第 {page_num}/{total_pages} 页，共 {total:,} 行")
        except Exception as e:
            st.error(f"读取数据失败：{e}")
            return

    else:
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
    "overview":    "📈 策略快照",
    "sector_heat": "🔥 板块热度",
    "db_viewer":   "🗄️ 数据库查看器",
}

_DETAIL_PAGES = {"detail", "sector_heat_detail"}

if _symbol_from_url:
    # Opened in a new tab via ?symbol=XXX — show stock detail directly, no nav
    show_detail(_symbol_from_url)
elif _sector_from_url:
    # Opened in a new tab via ?sector=XXX — show sector detail directly, no nav
    show_sector_heat_detail(_sector_from_url)
else:
    with st.sidebar:
        _current_page = st.session_state.page
        _nav_base = _current_page if _current_page not in _DETAIL_PAGES else (
            "overview" if _current_page == "detail" else "sector_heat"
        )
        nav_choice = st.radio(
            "页面",
            options=list(_NAV_LABELS.keys()),
            format_func=lambda k: _NAV_LABELS[k],
            index=list(_NAV_LABELS.keys()).index(_nav_base),
            key="_global_nav",
            label_visibility="collapsed",
        )
        if nav_choice != _nav_base:
            st.session_state.page = nav_choice
            st.session_state.selected_symbol = None
            st.session_state.selected_sector = None
            st.rerun()
        st.divider()

    # ---------------------------------------------------------------------------
    # Router
    # ---------------------------------------------------------------------------
    if st.session_state.page == "overview":
        show_overview()
    elif st.session_state.page == "sector_heat":
        show_sector_heat_overview()
    elif st.session_state.page == "sector_heat_detail":
        show_sector_heat_detail(st.session_state.selected_sector)
    elif st.session_state.page == "db_viewer":
        show_db_viewer()
    else:
        show_detail(st.session_state.selected_symbol)
