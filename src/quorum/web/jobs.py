from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    name: str
    status: JobStatus = JobStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None
    messages: list[str] = field(default_factory=list)


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, func: Callable, *args: Any, **kwargs: Any) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, name=name)
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run, args=(job, func, args, kwargs), daemon=True,
        )
        thread.start()
        return job_id

    def _run(self, job: Job, func: Callable, args: tuple, kwargs: dict) -> None:
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()
        try:
            job.result = func(*args, **kwargs)
            job.status = JobStatus.DONE
        except Exception as e:
            job.error = str(e)
            job.status = JobStatus.FAILED
        job.finished_at = datetime.now()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_all(self) -> list[Job]:
        return list(self._jobs.values())
