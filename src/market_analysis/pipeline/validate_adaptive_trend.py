"""Legacy validation alias for adaptive segmentation."""

from market_analysis.pipeline.validate_adaptive_segmentation import (
    run_adaptive_segmentation_validation,
)

run_adaptive_trend_validation = run_adaptive_segmentation_validation

__all__ = ["run_adaptive_segmentation_validation", "run_adaptive_trend_validation"]
