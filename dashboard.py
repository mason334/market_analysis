"""Deprecated local dashboard entrypoint.

The market_analysis project now only computes and stores indicator snapshots.
Display and interactive analysis live in the sibling investment_dashboard project.
"""

from __future__ import annotations

import typer


def main() -> None:
    typer.echo(
        "market_analysis no longer maintains a dashboard. "
        "Use the investment_dashboard project for display and analysis."
    )


if __name__ == "__main__":
    main()
