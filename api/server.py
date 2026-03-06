import os
import sys
import uuid
import queue
import threading
import json
import asyncio
from typing import Optional
from pathlib import Path

# Add the project root to sys.path so we can import from the backend
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

# ─── Job Store ────────────────────────────────────────────────────────────────
# Each job has:
#   status: "queued" | "running" | "done" | "error"
#   logs:   list of log strings seen so far
#   queue:  a thread-safe queue used to push new logs to SSE clients
#   result: final AgencyState dict (once done)
#   error:  error message (if failed)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _new_job() -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "status": "queued",
            "logs": [],
            "queue": queue.Queue(),
            "result": None,
            "error": None,
        }
    return job_id


def _push_log(job_id: str, message: str):
    with _lock:
        job = _jobs.get(job_id)
    if job:
        job["logs"].append(message)
        job["queue"].put(message)


def _finish_job(job_id: str, result: dict):
    with _lock:
        job = _jobs.get(job_id)
    if job:
        job["result"] = result
        job["status"] = "done"
        job["queue"].put("__DONE__")


def _fail_job(job_id: str, error: str):
    with _lock:
        job = _jobs.get(job_id)
    if job:
        job["error"] = error
        job["status"] = "error"
        job["queue"].put("__ERROR__")


# ─── Pipeline Runner ──────────────────────────────────────────────────────────

class _LogCapture:
    """
    Stdout proxy that intercepts write() to forward logs to the SSE queue,
    while delegating every other attribute (isatty, fileno, readable, etc.)
    to the real stdout so that Rich/CrewAI don't crash.
    """
    def __init__(self, job_id: str):
        self.job_id = job_id
        self._original_stdout = sys.stdout

    # ── intercept write so we can capture logs ──────────────────
    def write(self, text: str):
        stripped = text.strip()
        if stripped:
            _push_log(self.job_id, stripped)
        self._original_stdout.write(text)

    def flush(self):
        self._original_stdout.flush()

    # ── delegate EVERYTHING else to the real stdout ──────────────
    def __getattr__(self, name: str):
        return getattr(self._original_stdout, name)

    # ── context manager ──────────────────────────────────────────
    def __enter__(self):
        sys.stdout = self
        return self

    def __exit__(self, *args):
        sys.stdout = self._original_stdout


def _run_pipeline(job_id: str, niche_query: str):
    with _lock:
        _jobs[job_id]["status"] = "running"

    try:
        with _LogCapture(job_id):
            from schemas.models import AgencyState
            from workflows.graph import build_workflow

            _push_log(job_id, f"🚀 Initializing Nexus-Motion Agency")
            _push_log(job_id, f"🎯 Niche/Hook: {niche_query}")

            app_graph = build_workflow()

            initial_state = AgencyState(
                niche_query=niche_query,
                refinement_iterations=0
            )

            _push_log(job_id, "🤖 Agency Pipeline Active...")
            final_state = app_graph.invoke(initial_state)

        # Serialize result
        result = {
            "niche_query": final_state.get("niche_query") or niche_query,
            "strategy": None,
            "script": None,
            "score": None,
            "final_video_path": final_state.get("final_video_path"),
        }

        raw_strategy = final_state.get("strategy")
        if raw_strategy:
            result["strategy"] = raw_strategy.model_dump() if hasattr(raw_strategy, "model_dump") else dict(raw_strategy)

        raw_script = final_state.get("script")
        if raw_script:
            result["script"] = raw_script.model_dump() if hasattr(raw_script, "model_dump") else dict(raw_script)

        raw_score = final_state.get("score")
        if raw_score:
            result["score"] = raw_score.model_dump() if hasattr(raw_score, "model_dump") else dict(raw_score)

        _push_log(job_id, "✨ AGENCY WORKFLOW COMPLETE ✨")
        _finish_job(job_id, result)

    except Exception as e:
        _push_log(job_id, f"❌ Pipeline failed: {e}")
        _fail_job(job_id, str(e))


# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(title="Nexus-Motion API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    niche_query: str


@app.post("/api/run")
def start_run(req: RunRequest):
    """Start a new pipeline job and return a job_id."""
    if not req.niche_query.strip():
        raise HTTPException(status_code=400, detail="niche_query cannot be empty")

    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key or groq_key == "your_groq_api_key_here":
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured in .env"
        )

    job_id = _new_job()
    thread = threading.Thread(target=_run_pipeline, args=(job_id, req.niche_query), daemon=True)
    thread.start()

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/stream/{job_id}")
async def stream_logs(job_id: str):
    """SSE endpoint — streams log lines as they are produced."""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        log_queue = job["queue"]
        # First, send any already-captured logs so late-connecting clients catch up
        for existing_log in list(job["logs"]):
            yield {"data": existing_log}
            await asyncio.sleep(0)

        while True:
            try:
                msg = log_queue.get(timeout=0.5)
                if msg in ("__DONE__", "__ERROR__"):
                    yield {"data": msg}
                    break
                yield {"data": msg}
            except queue.Empty:
                if job["status"] in ("done", "error"):
                    yield {"data": "__DONE__" if job["status"] == "done" else "__ERROR__"}
                    break
                await asyncio.sleep(0.1)

    return EventSourceResponse(event_generator())


@app.get("/api/status/{job_id}")
def get_status(job_id: str):
    """Lightweight status poll endpoint."""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "error": job["error"],
    }


@app.get("/api/result/{job_id}")
def get_result(job_id: str):
    """Return the full result once the job is done."""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=202, detail=f"Job is still {job['status']}")
    return job["result"]


@app.get("/api/video/{job_id}")
def get_video(job_id: str):
    """Serve the final video file."""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=202, detail="Job not done yet")
    video_path = job["result"].get("final_video_path")
    if not video_path or not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(video_path, media_type="video/mp4")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "nexus-motion-api"}
