from __future__ import annotations

from market_analysis.pipeline._progress import format_eta, progress_log_fields


def test_progress_fields_are_human_readable() -> None:
    assert format_eta(236) == "3min56sec"
    assert format_eta(3792) == "1h03min12sec"
    assert progress_log_fields(30, 80, 100.0) == {
        "progress": "[30/80]",
        "completion": "37.5%",
        "eta": "2min47sec",
    }
    assert progress_log_fields(80, 80, 100.0)["eta"] == "0sec"
