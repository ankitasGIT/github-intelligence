import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepositoryCreate(BaseModel):
    owner: str
    name: str


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_node_id: str
    owner: str
    name: str
    full_name: str
    created_at: datetime
