# market_analysis

`market_analysis` is a Python backend for two-stage segmentation of daily equity and ETF
close-price histories:

1. **Adaptive segmentation** selects a continuous piecewise log-linear model.
2. **Pivot refinement** moves the internal boundaries to deterministic local close-price pivots
   and refits each pivot-to-pivot segment independently.

The project reads split-adjusted daily bars and universe membership directly from a
`market_data` PostgreSQL database. It writes immutable-by-key daily snapshots to a separate
`market_analysis` database. Visualization and interactive execution live in the sibling
`investment_dashboard` project.

This repository provides descriptive market analysis only. It does not implement trading
strategies, recommendations, portfolio management, order execution, or backtesting.

## Architecture

```text
market_data PostgreSQL
        |
        v
adaptive segmentation
        |
        +--> trend_segmentation_daily
        +--> trend_segment_daily
        |
        v
pivot refinement
        |
        +--> pivot_segmentation_daily
        +--> pivot_segment_daily
        |
        v
investment_dashboard / Market Snapshot
```

The dependency direction is deliberately small:

```text
cli -> pipeline -> indicators + db
indicators -> pandas/numpy only
db -> PostgreSQL connections and SQL
```

`investment_dashboard` does not import this package. It invokes explicit CLI commands and
reads the four snapshot tables through its own SQL layer.

## Repository layout

```text
market_analysis/
├── config/settings.yaml
├── docs/
│   ├── adaptive_segmentation_development.md
│   └── trend_pattern_v4_metric_spec.md
├── scripts/cron_run_segmentation_pipeline.sh
├── src/market_analysis/
│   ├── cli.py
│   ├── config.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── queries.py
│   │   └── schema.py
│   ├── indicators/
│   │   ├── adaptive_segmentation.py
│   │   └── pivot_segmentation.py
│   └── pipeline/
│       ├── _progress.py
│       ├── compute_adaptive_segmentation.py
│       ├── run_adaptive_segmentation.py
│       ├── run_pivot_segmentation.py
│       └── validate_adaptive_segmentation.py
└── tests/
```

The retained trend-pattern v4 document is a historical design record. That research route is
paused and its implementation is not part of the current package.

## Data model

The active schema contains four tables:

| Table | Primary key | Purpose |
|---|---|---|
| `trend_segmentation_daily` | `symbol, date, lookback_bars` | Adaptive model-selection summary and search audit |
| `trend_segment_daily` | `symbol, date, lookback_bars, segment_index` | Adaptive segment boundaries and statistics |
| `pivot_segmentation_daily` | `symbol, date, lookback_bars` | Pivot-refinement summary and source-version contract |
| `pivot_segment_daily` | `symbol, date, lookback_bars, segment_index` | Independently fitted pivot-to-pivot segments |

Schema initialization is idempotent. The cleanup that established this project scope does not
drop historical non-core tables from an existing database; it only stops creating and updating
them.

## Requirements

- Python 3.11 or newer
- PostgreSQL containing the `market_data` source tables
- `pip` and a virtual environment

Install the package in editable mode:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
```

Copy the environment template and set local credentials:

```bash
cp .env.example .env
```

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=market_analysis
DB_USER=postgres
DB_PASSWORD=your_password
SOURCE_DB_NAME=market_data
```

`.env` is ignored by Git. Do not commit real credentials.

## Commands

Initialize or update the four active tables:

```bash
market-analysis init-db
```

Run adaptive segmentation. Without `--date`, the pipeline uses all available source data and
anchors each symbol to its latest market date:

```bash
market-analysis run-adaptive-segmentation
market-analysis run-adaptive-segmentation --date 2026-09-18 --lookback 250
market-analysis run-adaptive-segmentation --date 2026-09-18 --lookback 250 --force
```

Run Pivot refinement from a persisted adaptive snapshot:

```bash
market-analysis run-pivot-segmentation
market-analysis run-pivot-segmentation --date 2026-09-18
```

Compute one read-only adaptive result as JSON:

```bash
market-analysis compute-adaptive-segmentation \
  --symbol AAPL \
  --lookback 250 \
  --min-segment-bars 5 \
  --max-segments 10 \
  --bic-penalty-multiplier 2.0
```

Generate a read-only validation report:

```bash
market-analysis validate-adaptive-segmentation --date 2026-09-18
```

The production cron helper runs adaptive segmentation first and Pivot refinement second:

```bash
scripts/cron_run_segmentation_pipeline.sh
```

## Algorithm notes

Adaptive segmentation uses continuous piecewise linear regression on log close prices. It
selects the segment count with a configurable BIC penalty. Exact enumeration is used when the
candidate count fits the configured budget; larger searches use a deterministic beam and local
refinement process. Search mode and convergence diagnostics are stored in the summary row.

Pivot refinement searches a fixed radius around each adaptive internal boundary using close
prices only. Each resulting pivot-to-pivot interval receives its own ordinary least-squares fit;
adjacent fitted lines are not required to meet. Segment data ownership follows `[start, end)`,
while the shared pivot endpoint is persisted separately.

See `docs/adaptive_segmentation_development.md` and the module docstrings for the complete
calculation contract.

## Development

```bash
python -m pytest
python -m ruff check .
```

Generated reports belong under `artifacts/`, which is ignored by Git.

## License

No open-source license has been granted yet. The repository may be publicly viewable, but reuse,
modification, and redistribution require the copyright holder's permission until a license is
added.
