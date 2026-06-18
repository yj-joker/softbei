"""出题 Agent 的输入/输出 schema。输出用 serialization_alias 发 camelCase（Java 读）。"""
from typing import List
from pydantic import BaseModel, Field


class QuizOption(BaseModel):
    key: str
    text: str


class QuizQuestionOut(BaseModel):
    topic: str
    question_type: str = Field(serialization_alias="questionType")  # single/multiple/judge
    stem: str
    options: List[QuizOption] = Field(default_factory=list)
    correct_answer: str = Field(serialization_alias="correctAnswer")
    explanation: str = ""
    sources: List[dict] = Field(default_factory=list)


class QuizGenerateResult(BaseModel):
    questions: List[QuizQuestionOut] = Field(default_factory=list)
