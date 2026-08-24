"""Worker entry point: `python -m nexus.worker.main`."""
from __future__ import annotations

import asyncio
import signal

from nexus.config import settings
from nexus.util.logging import configure_logging, get_logger
from nexus.worker.bus import bus
from nexus.worker.executor import WORKER_ID, execute_job

log = get_logger(__name__)


class Worker:
    def __init__(self, concurrency: int = 2):
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)
        self.running = True
        self.tasks: set[asyncio.Task] = set()

    def stop(self, *_: object) -> None:
        log.info("worker_stopping", extra={"in_flight": len(self.tasks)})
        self.running = False

    async def run(self) -> None:
        log.info("worker_started", extra={"worker_id": WORKER_ID, "concurrency": self.concurrency})
        while self.running:
            job_id = await bus.dequeue(timeout=5)
            if not job_id:
                continue
            await self.semaphore.acquire()
            task = asyncio.create_task(self._run_one(job_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        if self.tasks:
            log.info("worker_draining", extra={"in_flight": len(self.tasks)})
            await asyncio.gather(*self.tasks, return_exceptions=True)
        await bus.aclose()
        log.info("worker_stopped")

    async def _run_one(self, job_id: str) -> None:
        try:
            await execute_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker_job_error", extra={"job_id": job_id})
        finally:
            self.semaphore.release()


async def amain() -> None:
    configure_logging()
    from nexus.db.session import create_all

    if settings.database_url.startswith("sqlite"):
        await create_all()

    worker = Worker(concurrency=max(1, settings.max_parallel_shot_jobs // 3))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(sig, worker.stop)
    await worker.run()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
