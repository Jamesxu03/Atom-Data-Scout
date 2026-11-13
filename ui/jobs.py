"""Searches and research run in a background thread, so touching the page doesn't restart or
interrupt them; the job reports through its messages and version, which the page polls."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import streamlit as st

from worker import pipeline, sessions


@dataclass
class Job:
    kind: str  # "search" or "research"
    session_path: str
    messages: list[str] = field(default_factory=list)
    error: str | None = None
    done: bool = False
    started: float = field(default_factory=time.time)
    finished: float | None = None
    version: int = 0  # bumped each time the job saves the session

    def bump(self) -> None:
        self.version += 1

    @property
    def elapsed(self) -> int:
        return int((self.finished or time.time()) - self.started)


def current_job() -> Job | None:
    return st.session_state.get("job")


def busy() -> bool:
    job = current_job()
    return job is not None and not job.done


def start_job(kind: str, path: str, work: Callable[[Job], None]) -> None:
    """Run work(job) in a background thread; it reports through job.messages and job.bump."""
    if busy():
        return
    job = Job(kind=kind, session_path=path)

    def run():
        try:
            work(job)
        except Exception as e:
            job.error = f"{type(e).__name__}: {e}"
            try:  # keep what was saved, and say why it stopped
                s = sessions.load(path)
                if kind == "search":
                    s.meta.stage = "failed"
                s.meta.errors.append(f"{kind} failed: {job.error[:300]}")
                sessions.save(s)
            except Exception as save_error:  # an unreadable session must not hide the first error
                job.error += f" (and the session could not be updated: {type(save_error).__name__})"
        finally:
            job.finished = time.time()
            job.done = True

    threading.Thread(target=run, daemon=True).start()
    st.session_state.job = job


def start_search(path: str) -> None:
    start_job("search", path,
              lambda job: pipeline.search(sessions.load(path), progress=job.messages.append, on_update=job.bump))


def start_research(path: str, domains: list[str]) -> None:
    start_job("research", path,
              lambda job: pipeline.research(sessions.load(path), domains, progress=job.messages.append))
