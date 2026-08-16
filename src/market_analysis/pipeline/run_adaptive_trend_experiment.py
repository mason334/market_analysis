"""Legacy pipeline alias for adaptive segmentation."""

from market_analysis.pipeline.run_adaptive_segmentation import (
    run_adaptive_segmentation_pipeline,
)

run_adaptive_trend_experiment_pipeline = run_adaptive_segmentation_pipeline

__all__ = ["run_adaptive_segmentation_pipeline", "run_adaptive_trend_experiment_pipeline"]
