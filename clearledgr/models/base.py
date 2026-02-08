"""Shared model config and base types."""
from pydantic import BaseModel, ConfigDict


class CLBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
