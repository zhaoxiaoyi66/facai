from __future__ import annotations

from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from queue import Queue
from threading import Lock, Thread
import time
from typing import Callable, Generic, TypeVar


FMP_RATE_LIMIT = {
    "plan": "starter",
    "max_per_minute": 300,
    "safe_per_second": 4,
    "burst_per_minute": 240,
}


T = TypeVar("T")


@dataclass(frozen=True)
class QueuedRequest(Generic[T]):
    task: Callable[[], T]
    future: Future[T]


class FMPRequestQueue:
    """Single backend gateway for all FMP HTTP calls.

    Streamlit reruns the Python script often. This queue prevents those reruns
    from becoming uncontrolled outbound FMP traffic.
    """

    def __init__(
        self,
        safe_per_second: int = FMP_RATE_LIMIT["safe_per_second"],
        burst_per_minute: int = FMP_RATE_LIMIT["burst_per_minute"],
        worker_count: int = FMP_RATE_LIMIT["safe_per_second"],
    ) -> None:
        self.safe_per_second = safe_per_second
        self.burst_per_minute = burst_per_minute
        self.min_interval_seconds = 1.0 / safe_per_second
        self.requests: Queue[QueuedRequest] = Queue()
        self.started_at: deque[float] = deque()
        self.lock = Lock()
        self.workers = [
            Thread(target=self._worker, name=f"fmp-request-worker-{index + 1}", daemon=True)
            for index in range(max(1, worker_count))
        ]
        for worker in self.workers:
            worker.start()

    def submit(self, task: Callable[[], T], timeout_seconds: float | None = None) -> T:
        future: Future[T] = Future()
        self.requests.put(QueuedRequest(task=task, future=future))
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"FMP request did not finish within {timeout_seconds:.1f}s") from exc

    def stats(self) -> dict:
        with self.lock:
            now = time.monotonic()
            self._discard_old(now)
            started_last_minute = len(self.started_at)
        return {
            "plan": FMP_RATE_LIMIT["plan"],
            "queued": self.requests.qsize(),
            "started_last_minute": started_last_minute,
            "safe_per_second": self.safe_per_second,
            "burst_per_minute": self.burst_per_minute,
        }

    def _worker(self) -> None:
        while True:
            request = self.requests.get()
            try:
                self._wait_for_slot()
                if not request.future.set_running_or_notify_cancel():
                    continue
                request.future.set_result(request.task())
            except Exception as exc:
                request.future.set_exception(exc)
            finally:
                self.requests.task_done()

    def _wait_for_slot(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self._discard_old(now)
                wait_for_minute = 0.0
                if len(self.started_at) >= self.burst_per_minute:
                    wait_for_minute = 60.0 - (now - self.started_at[0])

                wait_for_second = 0.0
                if self.started_at:
                    wait_for_second = self.min_interval_seconds - (now - self.started_at[-1])

                wait_seconds = max(wait_for_minute, wait_for_second, 0.0)
                if wait_seconds <= 0:
                    self.started_at.append(now)
                    return
            time.sleep(min(wait_seconds, 1.0))

    def _discard_old(self, now: float) -> None:
        while self.started_at and now - self.started_at[0] >= 60.0:
            self.started_at.popleft()


_FMP_QUEUE: FMPRequestQueue | None = None
_FMP_QUEUE_LOCK = Lock()


def get_fmp_request_queue() -> FMPRequestQueue:
    global _FMP_QUEUE
    if _FMP_QUEUE is None:
        with _FMP_QUEUE_LOCK:
            if _FMP_QUEUE is None:
                _FMP_QUEUE = FMPRequestQueue()
    return _FMP_QUEUE
