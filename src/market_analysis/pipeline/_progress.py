from __future__ import annotations


def format_eta(seconds: float | None) -> str:
    """Format an ETA for compact human-readable CLI logs."""
    if seconds is None:
        return "calculating"
    remaining = max(0, round(seconds))
    hours, remainder = divmod(remaining, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}min{secs:02d}sec"
    if minutes:
        return f"{minutes}min{secs:02d}sec"
    return f"{secs}sec"


def progress_log_fields(
    processed: int,
    total: int,
    elapsed_seconds: float,
) -> dict[str, str]:
    """Return stable, human-readable progress fields for structlog events."""
    completion = 100.0 if total <= 0 else processed / total * 100.0
    if processed <= 0:
        eta_seconds = None
    elif processed >= total:
        eta_seconds = 0.0
    else:
        eta_seconds = elapsed_seconds / processed * (total - processed)
    return {
        "progress": f"[{processed}/{total}]",
        "completion": f"{completion:.1f}%",
        "eta": format_eta(eta_seconds),
    }
