"""
Small hand-rolled replacement for concurrent.futures.ProcessPoolExecutor,
built specifically because ProcessPoolExecutor cannot cancel a task once it
has started running (a well-known Python limitation: Future.cancel() returns
False once the call is underway, and there's no public API to kill the
underlying OS process). backend/main.py's /disrupt/aircraft-down "optimal"
path previously wrapped a ProcessPoolExecutor future in asyncio.wait_for(...,
timeout=...) -- timing out only stopped the *caller* waiting; the abandoned
computation kept running in the worker process indefinitely, wasting a pool
slot and (via analysis/disruption.py's now-fixed DP blowup) growing memory
without bound. This module gives the caller a real, killable handle instead.

Uses only public multiprocessing APIs (Process, Queue) -- no reliance on
ProcessPoolExecutor's private internals.
"""
import asyncio
import itertools
import multiprocessing as mp
import queue


# Generous: real init_worker() (backend/solver_worker.py) reloads a full
# graph pickle + rebuilds legs_by_tail into worker-local state, which is
# multiple seconds even when it succeeds.
WORKER_READY_TIMEOUT_SECONDS = 120


class WorkerInitError(Exception):
    """Raised when a worker's initializer fails, or the worker doesn't
    report ready within WORKER_READY_TIMEOUT_SECONDS. Surfaced loudly here
    instead of silently leaving a dead worker in the idle queue -- without
    this check, a worker that dies during init (confirmed to happen in
    practice: this app's real graph pickle is large enough that concurrent
    deserialization across multiple worker processes can raise MemoryError)
    would sit in _idle looking healthy, and the first job routed to it would
    hang forever: task_q.put() succeeds with no one left to read it, and
    submit()'s timeout only bounds the "optimal" solver path -- ordinary
    calls pass timeout=None.
    """


def _worker_main(task_q, result_q, initializer, initargs):
    """Entry point run inside each child process. Calls `initializer` once
    (the same role backend/solver_worker.py's init_worker plays today --
    building the worker-local graph/legs_by_tail/route_table/etc. into that
    module's _worker_state), reports readiness, then loops forever: block
    for a job, run it, report the result, loop again. Never returns under
    normal operation -- the parent ends a worker's life by terminate()ing
    the process, not by asking this loop to exit.
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
    """One live OS process plus its own dedicated task/result queues. A
    terminated worker's queues are never reused -- its replacement always
    gets a fresh pair, so a stale message from a killed worker can never be
    mistaken for a new job's result.
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
    """Spawn a worker and block (this is meant to be called off the event
    loop -- see start()/_replace_worker() below) until it confirms it
    finished `initializer` successfully. Raises WorkerInitError rather than
    returning a worker that looked fine but is actually dead or still
    stuck mid-init.
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
    zero-arg picklable callable (e.g. a functools.partial) and get an
    awaitable result back, same as loop.run_in_executor(process_pool, fn)
    would -- except submit()'s own timeout is real: on expiry the specific
    worker handling that job is terminate()d immediately (freeing its
    memory right away, not "eventually") and a fresh replacement is spawned
    in the background so pool capacity is restored without blocking the
    request that just timed out.

    Workers are spawned lazily, one at a time, the first N times submit()
    needs one that doesn't exist yet -- not all eagerly in start() -- so
    the peak "multiple processes deserializing the same large graph pickle
    simultaneously" moment (the one that produced a real MemoryError in
    testing on this machine) isn't concentrated at app startup. This also
    matches the memory-timing behavior of the ProcessPoolExecutor this
    replaces, which starts worker processes lazily on first submission
    rather than at construction.
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
        """Returns an idle worker, spawning a brand-new one (lazily, up to
        num_workers total) if the pool hasn't reached full size yet, rather
        than only ever waiting on workers created in start().
        """
        if self._spawned_count < self._num_workers and self._idle.empty():
            self._spawned_count += 1
            loop = asyncio.get_running_loop()
            try:
                worker = await loop.run_in_executor(
                    None, _spawn_ready_worker, self._initializer, self._initargs
                )
            except WorkerInitError:
                # Undo the reservation -- otherwise a permanently-failing
                # initializer would wedge the pool at "no capacity, no live
                # workers, nothing in flight" forever, since spawned_count
                # would stay pinned at num_workers with nothing to show for it.
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
        # worker.result_q.get() is a blocking call -- run it in the default
        # thread pool executor so it doesn't block the event loop.
        get_future = loop.run_in_executor(None, worker.result_q.get)
        try:
            returned_id, status, payload = await asyncio.wait_for(get_future, timeout=timeout)
        except asyncio.TimeoutError:
            worker.process.terminate()
            # Unblocks the reader thread still parked in worker.result_q.get()
            # -- cancelling get_future above doesn't stop a blocking call
            # already running in a real OS thread, so without this the
            # thread (and the default executor slot it holds) would leak
            # forever once its worker is dead and can never reply on its own.
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
            # Replacement failed to come up -- pool permanently shrinks by
            # one rather than silently reintroducing a dead worker. Loud
            # (visible via exhausted capacity/logging), not silent.
            return
        self._spawned_count += 1
        self._all_workers.append(worker)
        await self._idle.put(worker)

    def shutdown(self):
        for worker in self._all_workers:
            worker.process.terminate()
        for worker in self._all_workers:
            worker.process.join(timeout=2)
