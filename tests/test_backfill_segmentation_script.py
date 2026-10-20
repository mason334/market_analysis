from __future__ import annotations

import pytest

from scripts import backfill_segmentation_history as script


def test_job_lock_is_exclusive_and_released_on_close(monkeypatch, tmp_path) -> None:
    lock_file = tmp_path / "segmentation.lock"
    monkeypatch.setattr(script, "_LOCK_FILE", lock_file)

    first = script._acquire_lock()
    try:
        with pytest.raises(RuntimeError, match="Another segmentation job"):
            script._acquire_lock()
    finally:
        first.close()

    second = script._acquire_lock()
    second.close()
