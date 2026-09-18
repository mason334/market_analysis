"""提供自适应初分段与 Pivot 精炼的纯计算实现。"""

from market_analysis.segmentation.adaptive_segmentation import (
    compute_adaptive_segmentation,
    compute_adaptive_segmentation_snapshots,
    recommended_max_segments,
    reconstruct_adaptive_fit,
)
from market_analysis.segmentation.pivot_segmentation import compute_pivot_segmentation

__all__ = [
    "compute_adaptive_segmentation",
    "compute_adaptive_segmentation_snapshots",
    "compute_pivot_segmentation",
    "reconstruct_adaptive_fit",
    "recommended_max_segments",
]
