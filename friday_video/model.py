"""Provider-neutral data/state for Friday's native Quiet Video panel.

The model can describe an AI render job later, but this build performs
honest local motion rendering only: no network requests and no AI claims.
State is reconstructed from Friday's encrypted kv store by storage.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4


APP_NAME = "friday-video"
DEFAULT_OUTPUT_DIR = Path.home() / ".friday" / "generated" / "videos"
DEFAULT_STATE_PATH = Path.home() / ".friday" / "video-panel-state.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
STYLE_CHOICES = ("Mist", "Glass", "Paper", "Ember")
ASPECT_CHOICES = ("16:9", "1:1", "9:16")
QUALITY_CHOICES = ("Draft", "Studio")
MOTION_CHOICES = ("Still", "Push in", "Pan right", "Orbit", "Drift")
FORMAT_CHOICES = ("mp4", "gif")
FPS_CHOICES = (12, 18, 24, 30)
MAX_JOBS_PER_PROJECT = 40
MAX_PROMPT_CHARS = 4_000


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def seed_from_prompt(prompt: str) -> int:
    """Stable unsigned seed for preview/render reproducibility."""
    text = " ".join(prompt.split()).lower()
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")


@dataclass
class VideoRequest:
    prompt: str
    style: str = "Mist"
    aspect: str = "16:9"
    quality: str = "Studio"
    seconds: float = 4.0
    fps: int = 24
    motion: str = "Drift"
    seed: int | None = None
    source_image: str | None = None
    format: str = "mp4"

    def validated(self) -> "VideoRequest":
        prompt = " ".join(self.prompt.split()).strip()
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"The creative brief is limited to {MAX_PROMPT_CHARS} characters.")
        if self.style not in STYLE_CHOICES:
            raise ValueError("Choose a supported visual style.")
        if self.aspect not in ASPECT_CHOICES:
            raise ValueError("Choose a supported aspect ratio.")
        if self.quality not in QUALITY_CHOICES:
            raise ValueError("Choose a supported quality.")
        seconds = float(self.seconds)
        if not 1.0 <= seconds <= 10.0:
            raise ValueError("The local renderer supports 1–10 seconds.")
        fps = int(self.fps)
        if fps not in FPS_CHOICES:
            raise ValueError("Choose 12, 18, 24, or 30 fps.")
        if self.motion not in MOTION_CHOICES:
            raise ValueError("Choose a supported camera motion.")
        fmt = self.format.lower()
        if fmt not in FORMAT_CHOICES:
            raise ValueError("Choose MP4 or GIF output.")
        seed = self.seed
        if seed is None:
            seed = seed_from_prompt(prompt or self.style)
        seed = int(seed) & 0x7FFFFFFF
        source = self.source_image
        if source:
            source_path = Path(source).expanduser().resolve()
            if not source_path.is_file():
                raise ValueError("The reference image no longer exists.")
            if source_path.suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError("Reference images must be PNG, JPEG, WebP, or BMP.")
            source = str(source_path)
        else:
            source = None
        return VideoRequest(prompt=prompt, style=self.style, aspect=self.aspect,
                            quality=self.quality, seconds=seconds, fps=fps,
                            motion=self.motion, seed=seed, source_image=source,
                            format=fmt)

    @property
    def brief_label(self) -> str:
        return " ".join(self.prompt.split()) or f"{self.style} motion study"

    @property
    def frame_count(self) -> int:
        return max(1, round(self.seconds * self.fps))

    def wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RenderJob:
    request: dict[str, Any]
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "queued"
    progress: float = 0.0
    frames_done: int = 0
    frames_total: int = 1
    output_path: str | None = None
    poster_path: str | None = None
    error: str | None = None
    size_bytes: int = 0
    elapsed_seconds: float = 0.0
    created_at: str = field(default_factory=timestamp)
    finished_at: str | None = None

    def mark(self, status: str, **updates: Any) -> None:
        self.status = status
        for name, value in updates.items():
            if hasattr(self, name):
                setattr(self, name, value)
        if status in {"succeeded", "failed", "cancelled"}:
            self.finished_at = timestamp()

    @property
    def running(self) -> bool:
        return self.status in {"queued", "running"}

    @property
    def title(self) -> str:
        return " ".join(str(self.request.get("prompt", "")).split()) or "A quiet motion study"


@dataclass
class VideoProject:
    title: str = "Untitled film"
    brief: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    jobs: list[RenderJob] = field(default_factory=list)
    created_at: str = field(default_factory=timestamp)
    updated_at: str = field(default_factory=timestamp)

    def touch(self, brief: str | None = None) -> None:
        if brief is not None:
            self.brief = brief.strip()[:MAX_PROMPT_CHARS]
        name = " ".join(self.brief.split())
        if name:
            self.title = name[:34].rsplit(" ", 1)[0] + ("…" if len(name) > 34 else "")
        self.updated_at = timestamp()

    def add_job(self, job: RenderJob) -> None:
        self.jobs.insert(0, job)
        del self.jobs[MAX_JOBS_PER_PROJECT:]

    @property
    def latest_job(self) -> RenderJob | None:
        return self.jobs[0] if self.jobs else None

    @property
    def completed_jobs(self) -> list[RenderJob]:
        return [job for job in self.jobs if not job.running]


@dataclass
class AppSettings:
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    state_path: str = str(DEFAULT_STATE_PATH)


def _str(value: Any, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _int(value: Any, fallback: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = fallback
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _float(value: Any, fallback: float, minimum: float | None = None,
           maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = fallback
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _job_from_wire(raw: Any) -> RenderJob:
    if not isinstance(raw, dict):
        raise ValueError("Invalid render job")
    request = raw.get("request")
    if not isinstance(request, dict):
        raise ValueError("Render job is missing its request")
    job = RenderJob(request=dict(request), id=_str(raw.get("id")) or uuid4().hex,
                    status=_str(raw.get("status"), "failed"),
                    progress=_float(raw.get("progress"), 0.0, 0.0, 1.0),
                    frames_done=_int(raw.get("frames_done"), 0, 0),
                    frames_total=max(1, _int(raw.get("frames_total"), 1, 1)),
                    output_path=_str(raw.get("output_path")) or None,
                    poster_path=_str(raw.get("poster_path")) or None,
                    error=_str(raw.get("error")) or None,
                    size_bytes=_int(raw.get("size_bytes"), 0, 0),
                    elapsed_seconds=_float(raw.get("elapsed_seconds"), 0.0, 0.0),
                    created_at=_str(raw.get("created_at")) or timestamp(),
                    finished_at=_str(raw.get("finished_at")) or None)
    if job.status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
        raise ValueError("Invalid render status")
    # Interrupted jobs are not resumed after a GUI restart.
    if job.running:
        job.mark("failed", error="The renderer was closed before this job finished.",
                 progress=0.0)
    return job


def _project_from_wire(raw: Any) -> VideoProject:
    if not isinstance(raw, dict):
        raise ValueError("Invalid project")
    jobs_raw = raw.get("jobs", [])
    if not isinstance(jobs_raw, list):
        raise ValueError("Project jobs must be a list")
    project = VideoProject(title=_str(raw.get("title"), "Untitled film")[:80] or "Untitled film",
                           brief=_str(raw.get("brief"))[:MAX_PROMPT_CHARS],
                           id=_str(raw.get("id")) or uuid4().hex,
                           jobs=[_job_from_wire(item) for item in jobs_raw[:MAX_JOBS_PER_PROJECT]],
                           created_at=_str(raw.get("created_at")) or timestamp(),
                           updated_at=_str(raw.get("updated_at")) or timestamp())
    return project


def encode_state(projects: list[VideoProject], active_id: str | None,
                 settings: AppSettings | None = None) -> dict[str, Any]:
    """A JSON-safe structure stored inside Friday's encrypted kv store."""
    settings = settings or AppSettings()
    return {
        "version": 1,
        "active_id": active_id,
        "settings": asdict(settings),
        "projects": [asdict(project) for project in projects],
    }


def decode_state(data: Any, state_path: str = "") -> tuple[list[VideoProject], str, AppSettings]:
    """Validate and reconstruct panel state without touching a file."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unrecognized Quiet Video state record")
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("Projects must be a list")
    projects = [_project_from_wire(raw) for raw in raw_projects[:40]]
    if not projects:
        projects = [VideoProject()]
    seen: set[str] = set()
    for project in projects:
        if project.id in seen:
            raise ValueError("Duplicate project ID")
        seen.add(project.id)
    active = data.get("active_id")
    if not isinstance(active, str) or active not in seen:
        active = projects[0].id
    raw_settings = data.get("settings")
    if isinstance(raw_settings, dict):
        settings = AppSettings(output_dir=_str(raw_settings.get("output_dir"), str(DEFAULT_OUTPUT_DIR)),
                               state_path=state_path)
    else:
        settings = AppSettings(state_path=state_path)
    if not settings.output_dir:
        settings.output_dir = str(DEFAULT_OUTPUT_DIR)
    return projects, active, settings


def save_state(path: Path, projects: list[VideoProject], active_id: str | None,
               settings: AppSettings | None = None) -> None:
    """Atomically save panel state; a crashed render cannot damage history."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = encode_state(projects, active_id, settings)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".friday-video-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_state(path: Path) -> tuple[list[VideoProject], str | None, AppSettings]:
    path = Path(path).expanduser()
    if not path.exists():
        return [VideoProject()], None, AppSettings()
    data = json.loads(path.read_text(encoding="utf-8"))
    return decode_state(data, state_path=str(path))


def export_project_text(project: VideoProject) -> str:
    """Human-readable textual transcript of a project and its render history."""
    lines = [project.title, "=" * max(6, len(project.title)), ""]
    if project.brief:
        lines += ["Creative brief", "--------------", project.brief, ""]
    for job in project.jobs:
        request = job.request
        lines.append(f"{job.created_at} · {job.status} · {job.title}")
        controls = (f"Style {request.get('style', 'Mist')} · {request.get('aspect', '16:9')} · "
                    f"{request.get('seconds', '?')} s · {request.get('fps', '?')} fps · "
                    f"{request.get('motion', 'Drift')} · seed {request.get('seed', '—')}")
        lines.append(controls)
        if source_image := request.get("source_image"):
            lines.append(f"Reference image: {source_image}")
        if job.output_path:
            lines.append(f"Output: {job.output_path}")
        if job.error:
            lines.append(f"Error: {job.error}")
        lines.append("")
    return "\n".join(lines)
