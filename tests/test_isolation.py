import sys
import time

import pytest

from einvoice_reader.isolation import PoolTimeout, WorkerPool


def test_returns_result():
    pool = WorkerPool("test", 1, timeout_s=5)
    try:
        assert pool.call(abs, -3) == 3
    finally:
        pool.close()


def test_hard_timeout_kills_and_recovers():
    pool = WorkerPool("test", 1, timeout_s=1)
    try:
        pids = pool.worker_pids()
        start = time.monotonic()
        with pytest.raises(PoolTimeout):
            pool.call(time.sleep, 30)
        assert time.monotonic() - start < 3
        assert pool.worker_pids() != pids
        assert pool.call(abs, -1) == 1
    finally:
        pool.close()


def test_queued_jobs_do_not_time_out():
    from concurrent.futures import ThreadPoolExecutor

    pool = WorkerPool("test", 1, timeout_s=1)
    try:
        with ThreadPoolExecutor(3) as threads:
            results = list(threads.map(lambda _: pool.call(time.sleep, 0.6), range(3)))
        assert results == [None, None, None]
    finally:
        pool.close()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux")
def test_memory_limit_raises_memory_error():
    pool = WorkerPool("test", 1, timeout_s=10, memory_mb=512)
    try:
        with pytest.raises(MemoryError):
            pool.call(bytearray, 1024 * 2**20)
        assert pool.call(abs, -1) == 1
    finally:
        pool.close()
