from market_analysis.strategies.hurst import hurst
from market_analysis.strategies.ma_support import ma_support
from market_analysis.strategies.markov import markov
from market_analysis.strategies.sharpe import sharpe_ratio
from market_analysis.strategies.sudden_move import sudden_move
from market_analysis.strategies.sudden_surge import sudden_surge
from market_analysis.strategies.support_resistance import support_resistance
from market_analysis.strategies.variance_ratio import variance_ratio

STRATEGIES: dict[str, callable] = {
    "sudden_surge": sudden_surge,
    "sudden_move": sudden_move,
    "ma_support": ma_support,
    "variance_ratio": variance_ratio,
    "hurst": hurst,
    "markov": markov,
    "sharpe": sharpe_ratio,
    "support_resistance": support_resistance,
}
