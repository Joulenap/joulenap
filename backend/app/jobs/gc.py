"""Manual garbage-collection job.

Unlike the backup cycle, this does **not** manage power: GC's only power-managed path
is as a step inside the backup cycle. ``/api/gc/run`` (milestone 4) wraps this to run GC
against a PBS that is already awake — it fails if the PBS is unreachable.
"""

from __future__ import annotations

from ..config import Config
from ..db.models import RunStatus
from .backup_cycle import run_gc_step
from .deps import CycleDeps
from .recorder import RunRecorder


def run_gc(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Run GC on the already-awake PBS and record the result."""
    try:
        run_gc_step(config, recorder, deps)
        recorder.finish(RunStatus.SUCCESS)
    except Exception as exc:
        recorder.finish(RunStatus.FAILURE, error=str(exc))
