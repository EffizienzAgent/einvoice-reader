"""Process pools that never hang or crash the API: hard timeouts, bounded queue wait, restart."""

import logging
import os
import resource
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context

logger = logging.getLogger(__name__)

QUEUE_FACTOR = 2
MAX_ATTEMPTS = 4
STARTUP_TIMEOUT_S = 60


class PoolUnavailable(Exception):
    """The job could not run: the queue was full or a worker died twice."""


class PoolTimeout(Exception):
    """The job ran longer than the timeout; the workers were restarted."""


class PoolCrashed(Exception):
    """The job broke its worker process twice; retrying will not help."""


def _ping() -> bool:
    return True


def _watch_parent(parent: int) -> None:
    while True:
        time.sleep(2)
        if os.getppid() != parent:
            os._exit(1)


def _setup_worker(parent: int, memory_mb: int | None, initializer: Callable | None) -> None:
    threading.Thread(target=_watch_parent, args=(parent,), daemon=True).start()
    if memory_mb and sys.platform.startswith("linux"):
        limit = memory_mb * 2**20
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    if initializer is not None:
        initializer()


class WorkerPool:
    def __init__(
        self,
        name: str,
        workers: int,
        timeout_s: float,
        initializer: Callable | None = None,
        memory_mb: int | None = None,
    ):
        self.name = name
        self.workers = workers
        self.timeout_s = timeout_s
        self._initializer = initializer
        self._memory_mb = memory_mb
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(workers)
        self._pool = self._start()

    def _start(self) -> ProcessPoolExecutor:
        pool = ProcessPoolExecutor(
            max_workers=self.workers,
            mp_context=get_context("spawn"),
            initializer=_setup_worker,
            initargs=(os.getpid(), self._memory_mb, self._initializer),
        )
        for future in [pool.submit(_ping) for _ in range(self.workers)]:
            future.result(timeout=STARTUP_TIMEOUT_S)
        return pool

    def worker_pids(self) -> list[int]:
        return list(self._pool._processes)

    def call(self, fn: Callable, *args):
        if not self._slots.acquire(timeout=self.timeout_s * QUEUE_FACTOR):
            logger.warning("%s queue wait exceeded %ss", self.name, self.timeout_s * QUEUE_FACTOR)
            raise PoolUnavailable
        try:
            return self._call(fn, *args)
        finally:
            self._slots.release()

    def _call(self, fn: Callable, *args):
        crashes = 0
        for _attempt in range(MAX_ATTEMPTS):
            pool = self._pool
            try:
                future = pool.submit(fn, *args)
            except RuntimeError:
                self._restart(pool)
                continue
            try:
                return future.result(timeout=self.timeout_s)
            except FutureTimeout:
                logger.warning("%s job exceeded %ss, restarting workers", self.name, self.timeout_s)
                self._restart(pool)
                raise PoolTimeout from None
            except (BrokenProcessPool, CancelledError) as e:
                logger.warning("%s worker unavailable (%s)", self.name, type(e).__name__)
                if pool is self._pool:
                    crashes += 1
                    self._restart(pool)
                if crashes >= 2:
                    raise PoolCrashed from None
        raise PoolCrashed

    def _restart(self, failed: ProcessPoolExecutor) -> None:
        with self._lock:
            if self._pool is not failed:
                return
            for process in list(failed._processes.values()):
                process.kill()
            failed.shutdown(wait=False, cancel_futures=True)
            self._pool = self._start()

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
