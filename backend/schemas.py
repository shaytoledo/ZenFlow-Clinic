from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal


# ── Project schemas ──────────────────────────────────────────────────────────

class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    context: str = ""


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    context: str | None = None


class ProjectOut(ProjectBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectWithHistory(ProjectOut):
    prompts: list["PromptHistoryOut"] = []

    model_config = {"from_attributes": True}


# ── Prompt schemas ───────────────────────────────────────────────────────────

TargetModel = Literal["claude", "gpt-4", "gemini"]


class OptimizeRequest(BaseModel):
    user_input: str = Field(..., min_length=1, description="Simple user request in plain language")
    target_model: TargetModel = "claude"


class OptimizeResponse(BaseModel):
    optimized_prompt: str
    target_model: TargetModel
    history_id: int


class PromptHistoryOut(BaseModel):
    id: int
    user_input: str
    optimized_prompt: str
    target_model: str
    created_at: datetime

    model_config = {"from_attributes": True}


ProjectWithHistory.model_rebuild()
