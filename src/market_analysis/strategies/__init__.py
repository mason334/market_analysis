from market_analysis.strategies.ma_support import ma_support
from market_analysis.strategies.sudden_surge import sudden_surge

STRATEGIES: dict[str, callable] = {
    "sudden_surge": sudden_surge,
    "ma_support": ma_support,
}
