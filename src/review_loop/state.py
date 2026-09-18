"""Per-artifact state under the Git common dir, with atomic writes and one OS lock each.

Layout: <git-common-dir>/review-loop/
    index.json            {"<kind>:<repo-relative path>": "<artifact id>"}
    artifacts/<id>.json   ArtifactState
    locks/<id>.lock       flock target; released on process exit
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_LIMIT = 3


class StateError(Exception):
    pass


class LockedError(StateError):
    pass


@dataclass(frozen=True)
class RepoPaths:
    toplevel: Path
    common_dir: Path


def repo_paths(cwd: Path) -> RepoPaths:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as e:
        raise StateError(f"{cwd} is not inside a git repository") from e
    if len(out) != 2:
        raise StateError(f"{cwd} is not inside a git repository")
    toplevel = Path(out[0]).resolve()
    common = Path(out[1])
    if not common.is_absolute():
        common = (Path(cwd) / common).resolve()
    return RepoPaths(toplevel, common.resolve())


def repo_head(cwd: Path) -> str | None:
    """Current HEAD, recorded as review context only (never part of a plan version)."""
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


@dataclass
class ArtifactState:
    id: str
    kind: str
    path: str
    author: str | None = None
    reviewer: str | None = None
    session_id: str | None = None
    reviewer_cwd: str | None = None
    intent: str | None = None
    rounds: int = 0
    limit: int = DEFAULT_LIMIT
    reviewed_version: str | None = None
    last_status: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    retired_sessions: list[str] = field(default_factory=list)

    def reset(self) -> None:
        if self.session_id:
            self.retired_sessions.append(self.session_id)
        self.session_id = None
        self.rounds = 0
        self.limit = DEFAULT_LIMIT
        self.reviewed_version = None
        self.last_status = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactState:
        return cls(**d)


class Store:
    def __init__(self, common_dir: Path):
        self.root = common_dir / "review-loop"
        self.artifacts = self.root / "artifacts"
        self.locks = self.root / "locks"
        self.index_path = self.root / "index.json"
        self.index_lock_path = self.root / "index.lock"

    # --- paths ---------------------------------------------------------------
    def artifact_path(self, artifact_id: str) -> Path:
        return self.artifacts / f"{artifact_id}.json"

    def lock_path(self, artifact_id: str) -> Path:
        return self.locks / f"{artifact_id}.lock"

    def snapshot_dir(self, artifact_id: str) -> Path:
        return self.root / "snapshots" / artifact_id

    def write_snapshot(self, artifact_id: str, version: str, files: dict[str, str]) -> Path:
        """Freeze the reviewed inputs under snapshots/<id>/<version>/ and return that dir."""
        d = self.snapshot_dir(artifact_id) / version
        d.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8")
        return d

    # --- index ---------------------------------------------------------------
    @staticmethod
    def _key(kind: str, path: str) -> str:
        return f"{kind}:{path}"

    def _index(self) -> dict[str, str]:
        if not self.index_path.is_file():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _write_index(self, index: dict[str, str]) -> None:
        _atomic_write(self.index_path, index)

    # --- artifacts -----------------------------------------------------------
    def for_path(self, kind: str, path: str) -> tuple[ArtifactState, bool]:
        """Return (state, created). A known path reuses its artifact; unknown gets a new id."""
        existing = self._index().get(self._key(kind, path))
        if existing:
            return self.load(existing), False
        return ArtifactState(id=self._new_id(), kind=kind, path=path), True

    def reserve(self, kind: str, path: str) -> ArtifactState:
        """Look up or create the artifact for a path and persist it, under the index lock.

        Concurrent first calls on the same unknown path therefore agree on one id and
        then contend for that artifact's own lock instead of each inventing an id.
        """
        with self.lock_index():
            state, created = self.for_path(kind, path)
            if created:
                self.save(state)
            return state

    def _new_id(self) -> str:
        while True:
            candidate = secrets.token_hex(4)
            if not self.artifact_path(candidate).exists():
                return candidate

    def load(self, artifact_id: str) -> ArtifactState:
        p = self.artifact_path(artifact_id)
        if not p.is_file():
            raise StateError(f"unknown artifact {artifact_id!r}")
        return ArtifactState.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, state: ArtifactState) -> None:
        self.artifacts.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.artifact_path(state.id), state.to_dict())
        index = self._index()
        key = self._key(state.kind, state.path)
        if index.get(key) != state.id:
            index[key] = state.id
            self._write_index(index)

    def move(self, artifact_id: str, new_path: str) -> ArtifactState:
        """Re-map an artifact to a new path. Callers hold the artifact lock; the index
        lock is taken here so the shared mapping cannot interleave with a reservation."""
        with self.lock_index():
            return self._move(artifact_id, new_path)

    def _move(self, artifact_id: str, new_path: str) -> ArtifactState:
        state = self.load(artifact_id)
        index = self._index()
        owner = index.get(self._key(state.kind, new_path))
        if owner and owner != artifact_id:
            raise StateError(f"path {new_path!r} is already owned by artifact {owner!r}")
        index.pop(self._key(state.kind, state.path), None)
        state.path = new_path
        index[self._key(state.kind, new_path)] = artifact_id
        self.artifacts.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.artifact_path(state.id), state.to_dict())
        self._write_index(index)
        return state

    def resolve(self, ref: str) -> ArtifactState:
        """Accept an artifact id or a repo-relative path (any kind)."""
        if self.artifact_path(ref).is_file():
            return self.load(ref)
        for key, aid in self._index().items():
            if key.split(":", 1)[1] == ref:
                return self.load(aid)
        raise StateError(f"unknown artifact {ref!r}")

    # --- locking -------------------------------------------------------------
    @contextmanager
    def lock(self, artifact_id: str) -> Iterator[None]:
        with _flock(self.lock_path(artifact_id), f"artifact {artifact_id!r}"):
            yield

    @contextmanager
    def lock_index(self) -> Iterator[None]:
        with _flock(self.index_lock_path, "the artifact index"):
            yield


@contextmanager
def _flock(path: Path, what: str) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise LockedError(f"{what} is locked by another review-loop process") from e
        yield


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
