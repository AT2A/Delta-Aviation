"""
Hand-rolled replacement for concurrent.futures.ProcessPoolExecutor, whose
Future.cancel() can't stop a task once it's running -- a timed-out call kept
running in its worker indefinitely, leaking memory. This module terminates
the actual OS process on timeout instead. Uses only public multiprocessing
APIs (Process, Queue).
"""
import asyncio
import itertools
import multiprocessing as mp
import queue


# Generous: init_worker() (backend/solver_worker.py) reloads the lean data
# artifacts and rebuilds worker-local state, taking a few seconds even on success.
WORKER_READY_TIMEOUT_SECONDS = 120


class WorkerInitError(Exception):
    """Raised when a worker's initializer fails or doesn't report ready in
    time. Without this check a dead worker would sit in _idle looking
    healthy, and the first job routed to it would hang forever.
    """


def _worker_main(task_q, result_q, initializer, initargs):
    """Entry point run inside each child process: call `initializer` once,
    report readiness, then loop forever running jobs. The parent ends a
    worker's life by terminate()ing the process, not by signaling this loop.
    """
    try:
        initializer(*initargs)
    except Exception as exc:
        result_q.put(("__init_failed__", "__init_failed__", exc))
        return
    result_q.put(("__ready__", "__ready__", None))

    while True:
        job_id, fn = task_q.get()
        try:
            result_q.put((job_id, "ok", fn()))
        except Exception as exc:
            result_q.put((job_id, "error", exc))


class _Worker:
    """One live OS process plus its own task/result queues. A replacement
    worker always gets a fresh pair, so a stale message from a killed worker
    can never be mistaken for a new job's result.
    """

    def __init__(self, initializer, initargs):
        self.task_q = mp.Queue()
        self.result_q = mp.Queue()
        self.process = mp.Process(
            target=_worker_main,
            args=(self.task_q, self.result_q, initializer, initargs),
            daemon=True,
        )
        self.process.start()


def _spawn_ready_worker(initializer, initargs):
    """Spawn a worker and block (call off the event loop) until it confirms
    `initializer` finished. Raises WorkerInitError rather than returning a
    worker that's actually dead or still stuck mid-init.
    """
    worker = _Worker(initializer, initargs)
    try:
        _job_id, status, payload = worker.result_q.get(timeout=WORKER_READY_TIMEOUT_SECONDS)
    except queue.Empty:
        worker.process.terminate()
        raise WorkerInitError(
            f"worker did not report ready within {WORKER_READY_TIMEOUT_SECONDS}s"
        )
    if status == "__init_failed__":
        worker.process.join(timeout=2)
        raise WorkerInitError("worker initializer raised") from payload
    assert status == "__ready__"
    return worker


class KillableWorkerPool:
    """Fixed-size pool of persistent worker processes. Callers submit a
    zero-arg picklable callable and get an awaitable result, like
    loop.run_in_executor(process_pool, fn) -- except submit()'s timeout is
    real: on expiry the worker handling that job is terminate()d immediately
    and a replacement spawns in the background.

    Workers spawn lazily, one at a time, on the first N submit() calls that
    need one -- not all eagerly in start() -- so multiple processes never
    deserialize the large graph pickle simultaneously at startup (that
    produced a real MemoryError in testing).
    """

    def __init__(self, num_workers, initializer, initargs=()):
        self._num_workers = num_workers
        self._initializer = initializer
        self._initargs = initargs
        self._idle = None
        self._all_workers = []
        self._spawned_count = 0
        self._job_counter = itertools.count()

    def start(self):
        self._idle = asyncio.Queue()

    async def _get_idle_worker(self):
        """Returns an idle worker, lazily spawning a new one (up to
        num_workers total) if the pool hasn't reached full size yet.
        """
        if self._spawned_count < self._num_workers and self._idle.empty():
            self._spawned_count += 1
            loop = asyncio.get_running_loop()
            try:
                worker = await loop.run_in_executor(
                    None, _spawn_ready_worker, self._initializer, self._initargs
                )
            except WorkerInitError:
                # Undo the reservation, otherwise a permanently-failing
                # initializer wedges the pool at zero capacity forever.
                self._spawned_count -= 1
                raise
            self._all_workers.append(worker)
            return worker
        return await self._idle.get()

    async def submit(self, fn, timeout=None):
        worker = await self._get_idle_worker()
        job_id = next(self._job_counter)
        worker.task_q.put((job_id, fn))

        loop = asyncio.get_running_loop()
        # Blocking get() runs in the default thread pool so it doesn't block the event loop.
        get_future = loop.run_in_executor(None, worker.result_q.get)
        try:
            returned_id, status, payload = await asyncio.wait_for(get_future, timeout=timeout)
        except asyncio.TimeoutError:
            worker.process.terminate()
            # Unblocks the reader thread still parked in result_q.get() -- cancelling
            # get_future doesn't stop an in-progress blocking call in a real OS thread,
            # so without this the thread/executor slot would leak forever.
            worker.result_q.put((job_id, "cancelled", None))
            self._all_workers.remove(worker)
            self._spawned_count -= 1
            asyncio.create_task(self._replace_worker())
            raise

        await self._idle.put(worker)
        assert returned_id == job_id
        if status == "error":
            raise payload
        return payload

    async def _replace_worker(self):
        loop = asyncio.get_running_loop()
        try:
            worker = await loop.run_in_executor(
                None, _spawn_ready_worker, self._initializer, self._initargs
            )
        except WorkerInitError:
            # Replacement failed -- pool shrinks by one rather than silently
            # reintroducing a dead worker.
            return
        self._spawned_count += 1
        self._all_workers.append(worker)
        await self._idle.put(worker)

    def shutdown(self):
        for worker in self._all_workers:
            worker.process.terminate()
        for worker in self._all_workers:
            worker.process.join(timeout=2)
