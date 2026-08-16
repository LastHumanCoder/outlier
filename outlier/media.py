"""Turn a reel URL into the things a model can reason about: keyframes, cut
rhythm, and a transcript.

Frames and rhythm come from ffmpeg alone. That is a deliberate choice over the
opencv + scenedetect + faster-whisper stack: one binary that is already on the
machine, no wheels to build, and the scene scores ffmpeg prints for free are
exactly the cut-rhythm signal we want.

Transcripts come from VideoDB. Instagram's CDN URLs are public but expiring, so
VideoDB fetches them directly and we never need a logged-in session.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import requests

VIDEODB = "https://api.videodb.io"

# Repo root, used to resolve fixture video paths that are stored relative.
ROOT = Path(__file__).resolve().parent.parent

# Scene-change sensitivity for ffmpeg's `scene` filter. 0.3 is a middle setting:
# it catches hard cuts and ignores camera shake. Short-form reels cut hard, so
# false negatives are rarer than false positives here.
SCENE_THRESHOLD = 0.3
MAX_FRAMES = 6


# ---------------------------------------------------------------- ffmpeg


def download(url: str, dest: Path) -> Path:
    """Stream a video file to disk. Instagram CDN URLs expire, so this is
    always called right after the scrape, never from cached data.

    A local path is returned as-is, which is what makes fixture mode work with
    no network at all: the synthesized sample reels are already on disk.
    """
    if not url.startswith(("http://", "https://")):
        # Fixture paths are stored relative to the repo root so the committed
        # JSON works on any machine. An absolute path baked in at generation
        # time made the sample data unusable for everyone but its author.
        local = Path(url.removeprefix("file://"))
        if not local.is_absolute():
            local = ROOT / local
        if not local.exists():
            raise FileNotFoundError(f"Local video missing: {local}")
        return local

    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def cut_times(path: Path) -> list[float]:
    """Seconds at which the video cuts, via ffmpeg's scene detector.

    ffmpeg prints one `pts_time` line per frame that scores above the
    threshold; we read them off stderr. A single-shot talking head correctly
    returns an empty list.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-filter:v", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def cut_rhythm(path: Path) -> dict:
    """How fast this video cuts. This is the metric nobody's caption generator
    has, and it is usually the real difference between a reel that holds and
    one that does not."""
    dur = duration(path)
    cuts = cut_times(path)
    return {
        "durationSec": round(dur, 2),
        "cuts": len(cuts),
        "cutsPerSec": round(len(cuts) / dur, 2) if dur > 0 else 0.0,
        "avgShotSec": round(dur / (len(cuts) + 1), 2) if dur > 0 else 0.0,
        "firstCutSec": round(cuts[0], 2) if cuts else None,
    }


def keyframes(path: Path, out_dir: Path, max_frames: int = MAX_FRAMES) -> list[Path]:
    """One frame per cut, plus the opening frame.

    The opening frame is forced in because the hook is the single most
    important frame in a reel and a video with no cuts would otherwise yield
    nothing to look at.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = duration(path)
    times = [0.5] + [t for t in cut_times(path) if t > 1.0]

    # Nothing but the opener means a single continuous shot: sample it evenly so
    # the model still sees how the frame evolves.
    if len(times) == 1 and dur > 3:
        times = [round(dur * f, 2) for f in (0.02, 0.25, 0.5, 0.75, 0.95)]

    times = times[:max_frames]
    paths = []
    for i, t in enumerate(times):
        dst = out_dir / f"frame_{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(t), "-i", str(path), "-frames:v", "1",
             "-vf", "scale=512:-1", str(dst)],
            capture_output=True,
        )
        if dst.exists() and dst.stat().st_size > 0:
            paths.append(dst)
    return paths


# ---------------------------------------------------------------- VideoDB


def has_videodb() -> bool:
    return bool(os.environ.get("VIDEODB_API_KEY"))


def _vdb(path: str, method: str = "GET", body: dict | None = None, timeout: int = 240) -> dict:
    key = os.environ.get("VIDEODB_API_KEY")
    if not key:
        raise RuntimeError("VIDEODB_API_KEY not set")
    res = requests.request(
        method, f"{VIDEODB}{path}",
        headers={"x-access-token": key, "Content-Type": "application/json"},
        json=body, timeout=timeout,
    )
    data = res.json() if res.content else {}
    if not res.ok or data.get("success") is False:
        raise RuntimeError(f"VideoDB {path} -> {res.status_code}: {data.get('message', '')}")
    return data.get("data", data)


def _await_job(job_id: str, timeout: int = 300) -> dict:
    """Block until an async VideoDB job completes, then return its payload.

    `/collection/default/upload` returns a JOB id, not a video id. Passing that
    job id straight to the transcription endpoint fails with "Invalid video id",
    which is easy to misread as an auth or upload problem. The real video id
    only appears in the job payload once status flips to complete.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _vdb(f"/async-response/{job_id}", "GET", timeout=60)
        status = (job.get("status") or "").lower()
        if status == "complete":
            return (job.get("response") or {}).get("data") or job.get("data") or {}
        if status in {"failed", "error"}:
            raise RuntimeError(f"VideoDB job {job_id} failed: {job}")
        time.sleep(5)
    raise TimeoutError(f"VideoDB job {job_id} still processing after {timeout}s")


def transcript(url: str, name: str) -> str:
    """Upload by URL, wait for indexing, then pull the spoken transcript.

    Two separate waits happen here. First the upload job has to finish before a
    video id exists at all. Then VideoDB indexes speech asynchronously, so the
    transcript itself may still be unavailable for a while after that.
    """
    job = _vdb("/collection/default/upload", "POST",
               {"url": url, "media_type": "video", "name": name})
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError(f"VideoDB upload returned no job id: {job}")

    # A sync-completed upload hands back the video directly; otherwise poll.
    vid = job.get("id") if str(job.get("id", "")).startswith("m-") else None
    if not vid:
        vid = (_await_job(job_id) or {}).get("id")
    if not vid:
        raise RuntimeError(f"VideoDB upload job {job_id} produced no video id")

    # Two attempts, not four. The second passes force=true, which is what
    # VideoDB asks for when its first transcript job failed. Beyond that the
    # video genuinely has no speech, and a music-led reel is common enough that
    # spending another minute discovering it would slow every run.
    last = None
    for force in (False, True):
        try:
            _vdb(f"/video/{vid}/transcription", "POST", {"force": force})
            data = _vdb(f"/video/{vid}/transcription?force={str(force).lower()}", "GET",
                        timeout=60)
            text = (data.get("text") or "").strip()
            if text:
                return text
            raise RuntimeError("empty transcript")
        except Exception as e:  # noqa: BLE001 - the retry is the whole point
            last = e
            if not force:
                time.sleep(10)
    raise RuntimeError(f"No transcript ({last})")
