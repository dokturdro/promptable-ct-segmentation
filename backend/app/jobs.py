"""Minimal in-process job queue. One worker: a single GPU runs one model at a time."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

Status = Literal["queued", "running", "done", "failed"]


@dataclass
class Job:
    id: str
    kind: str
    status: Status = "queued"
    message: str = "Queued"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    # Voxel payload (uint8, C-order k/j/i) served separately as binary.
    array: np.ndarray | None = field(default=None, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "elapsed": round((self.finished or time.time()) - (self.started or self.created), 1),
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self, max_jobs: int = 50) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seg-worker")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def submit(self, kind: str, fn: Callable[[Job], None]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            # Drop the oldest finished jobs (and their voxel payloads).
            done = [j for j in self._jobs.values() if j.status in ("done", "failed")]
            excess = max(0, len(self._jobs) - self._max_jobs)
            for old in sorted(done, key=lambda j: j.created)[:excess]:
                del self._jobs[old.id]
        self._pool.submit(self._run, job, fn)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    @staticmethod
    def _run(job: Job, fn: Callable[[Job], None]) -> None:
        job.status, job.started, job.message = "running", time.time(), "Running"
        try:
            fn(job)
            job.status = "done"
            job.message = "Done"
        except Exception as e:  # surfaced to the client via GET /jobs/{id}
            traceback.print_exc()
            job.status, job.error, job.message = "failed", f"{type(e).__name__}: {e}", "Failed"
        finally:
            job.finished = time.time()


jobs = JobManager()
