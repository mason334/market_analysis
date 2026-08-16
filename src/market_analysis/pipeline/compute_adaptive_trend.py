"""Legacy pipeline alias for single-symbol adaptive segmentation."""

from market_analysis.pipeline.compute_adaptive_segmentation import (
    compute_adaptive_segmentation_for_symbol,
)

compute_adaptive_trend_for_symbol = compute_adaptive_segmentation_for_symbol

__all__ = ["compute_adaptive_segmentation_for_symbol", "compute_adaptive_trend_for_symbol"]
