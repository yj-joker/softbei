"""任务最终证据抽取的 Python/Java camelCase 契约。"""
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from typing_extensions import Annotated

PositiveJsonInt = Annotated[StrictInt, Field(gt=0)]

class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

class TaskEvidenceSnapshot(_Model):
    schema_version: Optional[str] = Field(default=None, alias="schemaVersion")
    task_id: Optional[PositiveJsonInt] = Field(default=None, alias="taskId")
    task_number: Optional[str] = Field(default=None, alias="taskNumber")
    device_id: Optional[str] = Field(default=None, alias="deviceId")
    device_name: Optional[str] = Field(default=None, alias="deviceName")
    fault_description: Optional[str] = Field(default=None, alias="faultDescription")
    maintenance_level: Optional[str] = Field(default=None, alias="maintenanceLevel")
    resolution_status: Literal["RESOLVED", "PARTIALLY_RESOLVED", "UNRESOLVED"] = Field(..., alias="resolutionStatus")
    final_fault_cause: Optional[str] = Field(default=None, alias="finalFaultCause")
    effective_measure: Optional[str] = Field(default=None, alias="effectiveMeasure")
    completion_summary: Optional[str] = Field(default=None, alias="completionSummary")
    created_at: Optional[str] = Field(default=None, alias="createdAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")
    resolved_at: Optional[str] = Field(default=None, alias="resolvedAt")
    snapshot_generated_at: Optional[str] = Field(default=None, alias="snapshotGeneratedAt")
    steps: List[Dict[str, Any]]
    report_images: List[str] = Field(default_factory=list, alias="reportImages")

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_null_collections(cls, value):
        if isinstance(value, dict) and value.get("reportImages") is None:
            value = {**value, "reportImages": []}
        return value

    @field_validator("report_images")
    @classmethod
    def persistent_report_images_only(cls, values):
        if any(str(value).strip().lower().startswith("data:") and ";base64," in str(value).lower() for value in values):
            raise ValueError("base64 data URLs are not persistent evidence")
        return values

class TaskEvidenceExtractionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_version: Literal["task-evidence-extraction.v1"] = Field(..., alias="schemaVersion")
    prompt_version: Literal["task-final-evidence.v1"] = Field(..., alias="promptVersion")
    request_id: str = Field(..., alias="requestId")
    task_id: PositiveJsonInt = Field(..., alias="taskId")
    evidence_version: PositiveJsonInt = Field(..., alias="evidenceVersion")
    snapshot: TaskEvidenceSnapshot

class Evidence(_Model):
    ref: str
    excerpt: str = ""
    step_id: Optional[str] = Field(default=None, alias="stepId")

class Warning(_Model):
    code: str
    message: str
    severity: str = "WARNING"

class Device(_Model):
    id: str
    name: str
    confirmed: bool = False
    evidence: List[Evidence] = Field(default_factory=list)

class Component(_Model):
    id: str
    name: str
    confirmed: bool = False
    evidence: List[Evidence] = Field(default_factory=list)

class Fault(_Model):
    id: str
    name: str
    confirmed: bool = False
    evidence: List[Evidence] = Field(default_factory=list)

class Solution(_Model):
    id: str
    title: str
    verified: bool = False
    source_type: str = Field(default="candidate", alias="sourceType")
    evidence: List[Evidence] = Field(default_factory=list)

class Relation(_Model):
    source_id: str = Field(..., alias="sourceId")
    target_id: str = Field(..., alias="targetId")
    type: str
    evidence: List[Evidence] = Field(default_factory=list)

class Candidates(_Model):
    devices: List[Device] = Field(default_factory=list)
    components: List[Component] = Field(default_factory=list)
    faults: List[Fault] = Field(default_factory=list)
    solutions: List[Solution] = Field(default_factory=list)
    relations: List[Relation] = Field(default_factory=list)

class ModelMetadata(_Model):
    name: str
    request_id: str = Field(..., alias="requestId")

class TaskEvidenceExtractionSuccess(_Model):
    model: ModelMetadata
    success: bool = True
    request_id: str = Field(..., alias="requestId")
    task_id: PositiveJsonInt = Field(..., alias="taskId")
    evidence_version: PositiveJsonInt = Field(..., alias="evidenceVersion")
    candidates: Candidates = Field(default_factory=Candidates)
    evidence: List[Evidence] = Field(default_factory=list)
    warnings: List[Warning] = Field(default_factory=list)

class TaskEvidenceExtractionFailure(_Model):
    success: bool = False
    request_id: str = Field(..., alias="requestId")
    task_id: PositiveJsonInt = Field(..., alias="taskId")
    evidence_version: PositiveJsonInt = Field(..., alias="evidenceVersion")
    error_code: str = Field(..., alias="errorCode")
    error: str
    retryable: bool = False
    warnings: List[Warning] = Field(default_factory=list)
