"""提供单只标的自适应分段的只读预览流程。

本模块读取指定标的的行情数据，按调用参数执行一次自适应分段，并返回摘要、
分段明细、拟合曲线和耗时；结果供 JSON CLI 使用，不写入分析数据库。
"""

from __future__ import annotations

from datetime import date
from time import perf_counter
from typing import Any

from market_analysis.config import settings
from market_analysis.db.queries import fetch_ohlcv
from market_analysis.segmentation.adaptive_segmentation import (
    compute_adaptive_segmentation,
    reconstruct_adaptive_fit,
)


def compute_adaptive_segmentation_for_symbol(
    symbol: str,
    *,
    lookback_bars: int,
    min_segment_bars: int,
    max_segments: int,
    bic_penalty_multiplier: float,
    target_date: date | None = None,
) -> dict[str, Any]:
    """Compute one read-only adaptive segmentation result."""
    params = dict(settings.indicators.get("adaptive_segmentation", {}))
    search_params = dict(params.get("search", {}))
    source = str(settings.pipeline.get("source", "tiingo"))
    frame = fetch_ohlcv(symbol.upper(), source=source)
    if target_date is not None and not frame.empty:
        frame = frame[frame.index.date <= target_date]
    if len(frame) < lookback_bars:
        raise ValueError(
            f"{symbol.upper()} has {len(frame)} bars, fewer than lookback {lookback_bars}."
        )

    started = perf_counter()
    summary, segments = compute_adaptive_segmentation(
        symbol.upper(),
        frame,
        lookback_bars=lookback_bars,
        min_segment_bars=min_segment_bars,
        max_segments=max_segments,
        bic_penalty_multiplier=bic_penalty_multiplier,
        search_params=search_params,
    )
    if summary is None:
        raise ValueError("Adaptive segmentation calculation returned no result.")
    fitted = reconstruct_adaptive_fit(frame, lookback_bars, segments)
    fitted_points = [
        {
            "date": index.date(),
            "close": float(row["close"]),
            "fitted_close": float(row["fitted_close"]),
        }
        for index, row in fitted.iterrows()
    ]
    return {
        "summary": summary,
        "segments": segments,
        "fitted_points": fitted_points,
        "elapsed_seconds": perf_counter() - started,
    }
