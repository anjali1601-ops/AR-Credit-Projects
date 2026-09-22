"""Tracing for every agent node: Langfuse or Phoenix when configured, local JSONL otherwise."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..config import Settings, get_settings


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


@dataclass
class SpanRecord:
    name: str
    run_id: str
    started_at: float
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def update(self, *, output: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> None:
        if output:
            self.output.update({k: _jsonable(v) for k, v in output.items()})
        if metadata:
            self.metadata.update({k: _jsonable(v) for k, v in metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "span": self.name,
            "duration_ms": round(self.duration_ms, 2),
            "input": self.input,
            "output": self.output,
            "metadata": self.metadata,
        }


class RunHandle:
    """One traced graph execution; `span()` wraps each agent node."""

    def __init__(self, tracer: "BaseTracer", run_id: str, name: str, metadata: dict[str, Any]):
        self.tracer = tracer
        self.run_id = run_id
        self.name = name
        self.metadata = metadata
        self.spans: list[SpanRecord] = []
        self.reference: str | None = None

    @contextmanager
    def span(self, name: str, *, input: dict[str, Any] | None = None) -> Iterator[SpanRecord]:
        record = SpanRecord(
            name=name,
            run_id=self.run_id,
            started_at=time.perf_counter(),
            input={k: _jsonable(v) for k, v in (input or {}).items()},
        )
        with self.tracer._backend_span(self, record):
            try:
                yield record
            finally:
                record.duration_ms = (time.perf_counter() - record.started_at) * 1000
                self.spans.append(record)
                self.tracer._on_span_end(self, record)


class BaseTracer:
    name = "noop"

    @contextmanager
    def run(self, name: str, *, run_id: str | None = None, metadata: dict[str, Any] | None = None) -> Iterator[RunHandle]:
        handle = RunHandle(self, run_id or uuid.uuid4().hex[:12], name, metadata or {})
        self._on_run_start(handle)
        try:
            yield handle
        finally:
            self._on_run_end(handle)

    # Hooks; the no-op tracer keeps everything in memory only.
    def _on_run_start(self, handle: RunHandle) -> None: ...

    def _on_run_end(self, handle: RunHandle) -> None: ...

    def _on_span_end(self, handle: RunHandle, record: SpanRecord) -> None: ...

    @contextmanager
    def _backend_span(self, handle: RunHandle, record: SpanRecord) -> Iterator[None]:
        yield


class LocalFileTracer(BaseTracer):
    """Default: append one JSON object per span to `.traces/<run_id>.jsonl`."""

    name = "local"

    def __init__(self, trace_dir: Path):
        self.trace_dir = Path(trace_dir)

    def _path(self, handle: RunHandle) -> Path:
        return self.trace_dir / f"{handle.run_id}.jsonl"

    def _on_run_start(self, handle: RunHandle) -> None:
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            handle.reference = str(self._path(handle))
            with self._path(handle).open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"run_id": handle.run_id, "trace": handle.name, "metadata": handle.metadata}) + "\n")
        except OSError:
            handle.reference = None

    def _on_span_end(self, handle: RunHandle, record: SpanRecord) -> None:
        if handle.reference is None:
            return
        try:
            with self._path(handle).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict()) + "\n")
        except OSError:
            handle.reference = None


class LangfuseTracer(BaseTracer):
    """Langfuse spans; requires LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY."""

    name = "langfuse"

    def __init__(self):
        from langfuse import Langfuse  # imported lazily: optional extra

        self._client = Langfuse()
        self._stack: list[Any] = []

    def _on_run_start(self, handle: RunHandle) -> None:
        handle.reference = f"langfuse:{handle.run_id}"

    @contextmanager
    def _backend_span(self, handle: RunHandle, record: SpanRecord) -> Iterator[None]:
        starter = getattr(self._client, "start_as_current_span", None)
        if starter is None:  # pragma: no cover - langfuse v2 fallback
            yield
            return
        with starter(name=record.name, input=record.input) as span:
            self._stack.append(span)
            try:
                yield
            finally:
                self._stack.pop()

    def _on_span_end(self, handle: RunHandle, record: SpanRecord) -> None:  # pragma: no cover - needs keys
        if self._stack:
            try:
                self._stack[-1].update(output=record.output, metadata=record.metadata)
            except Exception:  # noqa: BLE001
                pass

    def _on_run_end(self, handle: RunHandle) -> None:  # pragma: no cover - needs keys
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001
            pass


class PhoenixTracer(BaseTracer):
    """OpenTelemetry spans exported to an Arize Phoenix collector."""

    name = "phoenix"

    def __init__(self, project_name: str = "dunning-assistant"):
        from phoenix.otel import register  # imported lazily: optional extra

        self._provider = register(project_name=project_name, auto_instrument=True)
        self._tracer = self._provider.get_tracer(__name__)

    def _on_run_start(self, handle: RunHandle) -> None:
        handle.reference = f"phoenix:{handle.run_id}"

    @contextmanager
    def _backend_span(self, handle: RunHandle, record: SpanRecord) -> Iterator[None]:  # pragma: no cover - needs collector
        with self._tracer.start_as_current_span(record.name) as span:
            span.set_attribute("dunning.run_id", handle.run_id)
            for key, value in record.input.items():
                span.set_attribute(f"input.{key}", json.dumps(value) if isinstance(value, (dict, list)) else value)
            self._current = span
            try:
                yield
            finally:
                for key, value in record.output.items():
                    span.set_attribute(f"output.{key}", json.dumps(value) if isinstance(value, (dict, list)) else value)


def get_tracer(settings: Settings | None = None) -> BaseTracer:
    settings = settings or get_settings()
    choice = settings.tracing_backend.lower()
    if choice in ("none", "off", "noop"):
        return BaseTracer()
    if choice == "local":
        return LocalFileTracer(settings.trace_dir)
    if choice == "langfuse":
        return LangfuseTracer()
    if choice == "phoenix":
        return PhoenixTracer()
    if choice != "auto":
        raise ValueError(f"Unknown tracing backend {settings.tracing_backend!r}")

    if settings.langfuse_configured():
        try:
            return LangfuseTracer()
        except Exception:  # noqa: BLE001 - fall through to the local tracer
            pass
    if settings.phoenix_configured():
        try:
            return PhoenixTracer()
        except Exception:  # noqa: BLE001
            pass
    return LocalFileTracer(settings.trace_dir)


def read_trace(path: Path | str) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
