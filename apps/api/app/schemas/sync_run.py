import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SyncRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    stats: dict[str, Any] | None
