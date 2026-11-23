"""Provider-neutral data/state for Friday's native Quiet Image panel.

The panel is cloud-only: providers do the network calls (see
friday_image/providers.py); this module owns provider-neutral request/job
shapes. State is reconstructed from Friday's encrypted kv store by storage.py.
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


APP_NAME = "friday-image"
DEFAULT_OUTPUT_DIR = Path.home() / ".friday" / "generated" / "images"
DEFAULT_STATE_PATH = Path.home() / ".friday" / "image-panel-state.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
STYLE_CHOICES = ("Mist", "Glass", "Paper", "Ember")
ASPECT_CHOICES = ("16:9", "4:3", "1:1", "3:4", "9:16")
QUALITY_CHOICES = ("Draft", "Studio")
COMPOSITION_CHOICES = ("Horizon", "Center", "Rings", "Cuts")
FORMAT_CHOICES = ("png", "jpg", "webp")
MAX_JOBS_PER_PROJECT = 40
MAX_PROMPT_CHARS = 4_000


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def seed_from_prompt(prompt: str) -> int:
    """Stable unsigned seed for preview/render reproducibility."""
    text = " ".join(prompt.split()).lower()
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")


@dataclass
class ImageRequest:
    prompt: str
    style: str = "Mist"
    aspect: str = "16:9"
    quality: str = "Studio"
    composition: str = "Horizon"
    detail: float = 42.0
    seed: int | None = None
    source_image: str | None = None
    format: str = "png"

    def validated(self) -> "ImageRequest":
        prompt = " ".join(self.prompt.split()).strip()
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"The creative brief is limited to {MAX_PROMPT_CHARS} characters.")
        if self.style not in STYLE_CHOICES:
            raise ValueError("Choose a supported visual style.")
        if self.aspect not in ASPECT_CHOICES:
            raise ValueError("Choose a supported aspect ratio.")
        if self.quality not in QUALITY_CHOICES:
            raise ValueError("Choose a supported quality.")
        if self.composition not in COMPOSITION_CHOICES:
            raise ValueError("Choose a supported composition.")
        detail = float(self.detail)
        if not 10.0 <= detail <= 100.0:
            raise ValueError("Detail must stay between 10 and 100.")
        fmt = self.format.lower().lstrip(".")
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in FORMAT_CHOICES:
            raise ValueError("Choose PNG, JPEG, or WebP output.")
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
        return ImageRequest(prompt=prompt, style=self.style, aspect=self.aspect,
                            quality=self.quality, composition=self.composition,
                            detail=detail, seed=seed, source_image=source,
                            format=fmt)

    @property
    def prompt_label(self) -> str:
        return " ".join(self.prompt.split()) or f"{self.style} art study"

    @property
    def steps_total(self) -> int:
        return 7 + int(round(self.detail / 10.0))

    def wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImageJob:
    request: dict[str, Any]
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "queued"
    progress: float = 0.0
    steps_done: int = 0
    steps_total: int = 1
    output_path: str | None = None
    thumbnail_path: str | None = None
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
        return " ".join(str(self.request.get("prompt", "")).split()) or "A quiet art study"


@dataclass
class ImageProject:
    title: str = "Untitled artwork"
    brief: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    jobs: list[ImageJob] = field(default_factory=list)
    created_at: str = field(default_factory=timestamp)
    updated_at: str = field(default_factory=timestamp)

    def touch(self, brief: str | None = None) -> None:
        if brief is not None:
            self.brief = brief.strip()[:MAX_PROMPT_CHARS]
        name = " ".join(self.brief.split())
        if name:
            self.title = name[:34].rsplit(" ", 1)[0] + "…" if len(name) > 34 else name
        self.updated_at = timestamp()

    def add_job(self, job: ImageJob) -> None:
        self.jobs.insert(0, job)
        del self.jobs[MAX_JOBS_PER_PROJECT:]

    @property
    def latest_job(self) -> ImageJob | None:
        return self.jobs[0] if self.jobs else None

    @property
    def completed_jobs(self) -> list[ImageJob]:
        return [job for job in self.jobs if not job.running]


@dataclass
class AppSettings:
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    state_path: str = str(DEFAULT_STATE_PATH)
    # Engine picker choice: "auto" (first usable provider) or a Settings
    # Image Generation entry id. engine_b + compare power side-by-side.
    engine: str = "auto"
    engine_b: str = "auto"
    compare: bool = False
    engine_open: bool = False


def _str(value: Any, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _int(value: Any, fallback: int, minimum: int | None = None,
         maximum: int | None = None) -> int:
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


def _job_from_wire(raw: Any) -> ImageJob:
    if not isinstance(raw, dict):
        raise ValueError("Invalid image job")
    request = raw.get("request")
    if not isinstance(request, dict):
        raise ValueError("Image job is missing its request")
    job = ImageJob(
        request=dict(request),
        id=_str(raw.get("id")) or uuid4().hex,
        status=_str(raw.get("status"), "failed"),
        progress=_float(raw.get("progress"), 0.0, 0.0, 1.0),
        steps_done=_int(raw.get("steps_done"), 0, 0),
        steps_total=max(1, _int(raw.get("steps_total"), 1, 1)),
        output_path=_str(raw.get("output_path")) or None,
        thumbnail_path=_str(raw.get("thumbnail_path")) or None,
        error=_str(raw.get("error")) or None,
        size_bytes=_int(raw.get("size_bytes"), 0, 0),
        elapsed_seconds=_float(raw.get("elapsed_seconds"), 0.0, 0.0),
        created_at=_str(raw.get("created_at")) or timestamp(),
        finished_at=_str(raw.get("finished_at")) or None,
    )
    if job.status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
        raise ValueError("Invalid render status")
    # An interrupted image job is never silently resumed on panel restart.
    if job.running:
        job.mark("failed", error="The renderer was closed before this job finished.",
                 progress=0.0)
    return job


def _project_from_wire(raw: Any) -> ImageProject:
    if not isinstance(raw, dict):
        raise ValueError("Invalid project")
    jobs_raw = raw.get("jobs", [])
    if not isinstance(jobs_raw, list):
        raise ValueError("Project jobs must be a list")
    return ImageProject(
        title=_str(raw.get("title"), "Untitled artwork")[:80] or "Untitled artwork",
        brief=_str(raw.get("brief"))[:MAX_PROMPT_CHARS],
        id=_str(raw.get("id")) or uuid4().hex,
        jobs=[_job_from_wire(item) for item in jobs_raw[:MAX_JOBS_PER_PROJECT]],
        created_at=_str(raw.get("created_at")) or timestamp(),
        updated_at=_str(raw.get("updated_at")) or timestamp(),
    )


def encode_state(projects: list[ImageProject], active_id: str | None,
                 settings: AppSettings | None = None) -> dict[str, Any]:
    """A JSON-safe structure stored inside Friday's encrypted kv store."""
    settings = settings or AppSettings()
    return {
        "version": 1,
        "active_id": active_id,
        "settings": asdict(settings),
        "projects": [asdict(project) for project in projects],
    }


def decode_state(data: Any, state_path: str = "") -> tuple[list[ImageProject], str, AppSettings]:
    """Validate and reconstruct panel state without touching a file."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unrecognized Quiet Image state record")
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("Projects must be a list")
    projects = [_project_from_wire(raw) for raw in raw_projects[:40]]
    if not projects:
        projects = [ImageProject()]
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
        settings = AppSettings(output_dir=_str(raw_settings.get("output_dir"),
                                                  str(DEFAULT_OUTPUT_DIR)),
                               state_path=state_path,
                               engine=_str(raw_settings.get("engine")) or "auto",
                               engine_b=_str(raw_settings.get("engine_b")) or "auto",
                               compare=bool(raw_settings.get("compare", False)),
                               engine_open=bool(raw_settings.get("engine_open", False)))
    else:
        settings = AppSettings(state_path=state_path)
    if not settings.output_dir:
        settings.output_dir = str(DEFAULT_OUTPUT_DIR)
    return projects, active, settings


def save_state(path: Path, projects: list[ImageProject], active_id: str | None,
               settings: AppSettings | None = None) -> None:
    """Atomically save plain test/export state (not Friday's UI store)."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = encode_state(projects, active_id, settings)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".friday-image-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_state(path: Path) -> tuple[list[ImageProject], str | None, AppSettings]:
    path = Path(path).expanduser()
    if not path.exists():
        return [ImageProject()], None, AppSettings()
    data = json.loads(path.read_text(encoding="utf-8"))
    return decode_state(data, state_path=str(path))


def export_project_text(project: ImageProject) -> str:
    """Human-readable textual export of a project and its render history."""
    lines = [
        f"Quiet Image — {project.title}",
        f"Created: {project.created_at}",
        f"Updated: {project.updated_at}",
        "",
        "Creative brief",
        "--------------",
        project.brief or "(empty)",
        "",
        "Render history",
        "--------------",
    ]
    for job in project.jobs:
        request = job.request
        lines.extend([
            f"- {job.status.upper()} · {request.get('style', '?')} · "
            f"{request.get('aspect', '?')} · {request.get('format', '?')} · "
            f"seed {request.get('seed', '?')}",
            f"  Created: {job.created_at}",
        ])
        if job.output_path:
            lines.append(f"  Image: {job.output_path}")
        if job.error:
            lines.append(f"  Error: {job.error}")
    return "\n".join(lines).rstrip() + "\n"
