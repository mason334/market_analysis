"""Legacy import aliases for the renamed adaptive segmentation module."""

from market_analysis.indicators.adaptive_segmentation import (
    compute_adaptive_segmentation,
    compute_adaptive_segmentation_snapshots,
    recommended_max_segments,
    reconstruct_adaptive_fit,
)

compute_adaptive_trend_experiment = compute_adaptive_segmentation_snapshots

__all__ = [
    "compute_adaptive_segmentation",
    "compute_adaptive_segmentation_snapshots",
    "compute_adaptive_trend_experiment",
    "reconstruct_adaptive_fit",
    "recommended_max_segments",
]
