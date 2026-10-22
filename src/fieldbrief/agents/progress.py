from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

PROGRESS_EXCHANGE = "fieldbrief.progress"


class Stage(StrEnum):
    RESOLVING = "resolving"
    DISCOVERING = "discovering"
    RESEARCHING = "researching"
    VALIDATING = "validating"
    ANALYZING = "analyzing"
    SYNTHESIZING = "synthesizing"
    RENDERING = "rendering"
    DONE = "done"
    FAILED = "failed"


class ProgressEvent(BaseModel):
    report_id: str
    stage: Stage
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


def routing_key_for(report_id: str) -> str:
    return f"report.{report_id}.progress"


class ProgressSink(Protocol):
    async def emit(self, stage: Stage, message: str) -> None: ...


class NullProgressSink:
    async def emit(self, stage: Stage, message: str) -> None:
        return None


class LoggingProgressSink:
    def __init__(self, report_id: str) -> None:
        self.report_id = report_id

    async def emit(self, stage: Stage, message: str) -> None:
        print(f"[{self.report_id}] {stage.value}: {message}")
