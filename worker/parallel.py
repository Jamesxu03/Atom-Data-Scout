"""Run independent tasks in threads, keeping whatever finished if the Gemini quota runs out."""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from worker.llm import QuotaExceeded

T = TypeVar("T")
_SKIPPED = object()


@dataclass
class Batch(Generic[T]):
    results: list[tuple[str, T]] = field(default_factory=list)  # (label, result), completion order
    errors: list[str] = field(default_factory=list)
    quota_error: str | None = None  # set if a task hit QuotaExceeded; later tasks were skipped


def run_parallel(
    tasks: list[tuple[str, Callable[[], T]]],
    workers: int,
    progress: Callable[[str], None] = print,
) -> Batch[T]:
    """tasks: (label, zero-arg function). A failed task is logged in errors and skipped."""
    batch: Batch[T] = Batch()
    stop = threading.Event()

    def guarded(label: str, fn: Callable[[], T]):
        if stop.is_set():
            return _SKIPPED
        progress(label)
        try:
            return fn()
        except QuotaExceeded:
            stop.set()  # set in the worker thread, before it can pick up another task
            raise

    if not tasks:
        return batch
    with ThreadPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
        futures = {pool.submit(guarded, label, fn): label for label, fn in tasks}
        for future in as_completed(futures):
            label = futures[future]
            try:
                result = future.result()
            except QuotaExceeded as e:
                stop.set()
                batch.quota_error = batch.quota_error or f"{label}: {e}"
                continue
            except Exception as e:  # one failed task shouldn't sink the others
                batch.errors.append(f"{label}: {type(e).__name__}: {str(e)[:200]}")
                continue
            if result is not _SKIPPED:
                batch.results.append((label, result))
    return batch
