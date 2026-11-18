"""Journalisation structurée JSON et métriques d'exécution par étape de pipeline."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class StepMetric:
    """Métriques mesurées pour une étape individuelle du pipeline."""

    step: str
    duration_ms: float
    rows_in: int = 0
    rows_out: int = 0
    status: str = "ok"
    details: dict[str, object] = field(default_factory=dict)

    @property
    def throughput_rows_per_sec(self) -> float:
        if self.duration_ms <= 0 or self.rows_in <= 0:
            return 0.0
        return round(self.rows_in / (self.duration_ms / 1000.0), 2)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["throughput_rows_per_sec"] = self.throughput_rows_per_sec
        return payload


@dataclass(slots=True)
class RunTelemetry:
    """Agrégat complet d'observabilité pour une exécution du pipeline."""

    pipeline_name: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    total_duration_ms: float = 0.0
    steps: list[StepMetric] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "pipeline_name": self.pipeline_name,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": round(self.total_duration_ms, 3),
            "steps": [step.to_dict() for step in self.steps],
        }


class JsonFormatter(logging.Formatter):
    """Formateur produisant une ligne JSON structurée par événement de log."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "structured_data", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_structured_logger(
    name: str = "datapulse",
    *,
    enabled: bool = False,
    level: int = logging.INFO,
) -> logging.Logger:
    """Retourne un logger JSON (silencieux via NullHandler si enabled=False)."""
    logger = logging.getLogger(name)
    logger.handlers.clear()
    if enabled:
        handler: logging.Handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.NullHandler()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


class PipelineTracker:
    """Chronomètre chaque étape du pipeline et émet des logs structurés."""

    def __init__(
        self,
        pipeline_name: str,
        *,
        run_id: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.telemetry = RunTelemetry(
            pipeline_name=pipeline_name,
            run_id=run_id or str(uuid.uuid4()),
        )
        self.logger = logger or get_structured_logger(enabled=False)
        self._run_start = time.perf_counter()

    @contextmanager
    def track_step(
        self,
        step_name: str,
        *,
        rows_in: int = 0,
    ) -> Iterator[StepMetric]:
        """Mesure le temps d'exécution et le statut d'une étape du pipeline."""
        metric = StepMetric(step=step_name, duration_ms=0.0, rows_in=rows_in)
        start = time.perf_counter()
        try:
            yield metric
        except Exception as exc:
            metric.status = "error"
            metric.details["error_type"] = type(exc).__name__
            metric.details["error_message"] = str(exc)
            raise
        finally:
            metric.duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
            self.telemetry.steps.append(metric)
            self.logger.info(
                f"step.{step_name}.{metric.status}",
                extra={
                    "structured_data": {
                        "run_id": self.telemetry.run_id,
                        "pipeline": self.telemetry.pipeline_name,
                        "step": step_name,
                        "status": metric.status,
                        "duration_ms": metric.duration_ms,
                        "rows_in": metric.rows_in,
                        "rows_out": metric.rows_out,
                        "throughput_rows_per_sec": metric.throughput_rows_per_sec,
                    }
                },
            )

    def finalize(self) -> RunTelemetry:
        """Clôture la télémétrie globale du run."""
        self.telemetry.finished_at = datetime.now(timezone.utc).isoformat()
        self.telemetry.total_duration_ms = round(
            (time.perf_counter() - self._run_start) * 1000.0, 3
        )
        return self.telemetry
