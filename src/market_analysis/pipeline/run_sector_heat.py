from __future__ import annotations

from datetime import date, timedelta

import structlog

from market_analysis.analytics.sector_heat import compute_sector_heat
from market_analysis.db.queries import (
    fetch_all_universe_constituents,
    fetch_constituent_turnover_batch,
    upsert_sector_heat_daily,
)

log = structlog.get_logger(__name__)

# Fetch 120 calendar days ≈ 84 trading days; enough for MA20 + 60-day z-score
_LOOKBACK_CALENDAR_DAYS = 120


def run_sector_heat_pipeline(source: str = "tiingo") -> int:
    """
    For each universe_ticker in universe_constituents:
      1. Fetch constituent stocks' historical turnover (close * volume)
      2. Compute sector heat metrics for the latest date
      3. Upsert into sector_heat_daily

    Returns the number of universe_tickers successfully written.
    """
    constituents_map = fetch_all_universe_constituents()
    if not constituents_map:
        log.warning("sector_heat.pipeline.no_constituents")
        return 0

    start_date = date.today() - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
    total = 0

    for universe_ticker, stock_tickers in sorted(constituents_map.items()):
        try:
            wide_df = fetch_constituent_turnover_batch(
                stock_tickers=stock_tickers,
                start_date=start_date,
                source=source,
            )
            row = compute_sector_heat(universe_ticker, wide_df)
            if row is not None:
                upsert_sector_heat_daily(row)
                total += 1
                log.info(
                    "sector_heat.pipeline.done",
                    ticker=universe_ticker,
                    date=str(row["date"]),
                    constituents=row["constituent_count"],
                    ratio=f"{row['turnover_ratio']:.2f}" if row.get("turnover_ratio") else "N/A",
                    zscore=f"{row['turnover_zscore']:.2f}" if row.get("turnover_zscore") else "N/A",
                )
            else:
                log.info("sector_heat.pipeline.skipped", ticker=universe_ticker)
        except Exception:
            log.exception("sector_heat.pipeline.error", ticker=universe_ticker)

    log.info("sector_heat.pipeline.complete", total_upserted=total)
    return total
