from typing import Any, Optional

from pydantic import BaseModel, Field


class VoiceTaskRequest(BaseModel):
    session_id: str = Field(default="", description="Voice task identifier (task-voice-{taskId}).")
    task_id: Optional[int] = None
    user_id: Optional[int] = None
    transcript: str = Field(..., min_length=1, description="ASR final transcript.")
    focused_step_id: Optional[int] = None
    confirmed: bool = False
    override: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class VoiceTaskDecision(BaseModel):
    action_label: str = Field(default="无有效操作")
    action: str = Field(default="no_op")
    reply_text: str = Field(default="")
    target_step_id: Optional[int] = None
    target_step_order: Optional[int] = None
    needs_confirmation: bool = False
    override_recommended: bool = False
    can_execute: bool = False
    state_change: Optional[str] = None
    risk_level: str = "unknown"
    risk_reason: str = ""
    confidence: float = 0.0
    audit_reason: str = ""
    execution_payload: dict[str, Any] = Field(default_factory=dict)
    summary_update: Optional[str] = None
    raw_model_output: Optional[str] = None

