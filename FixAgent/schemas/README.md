# Schemas 模块

## 模块概述

Schemas 模块定义所有请求和响应的数据模型，是系统的**类型安全层**。本模块使用 Pydantic v2 定义数据模型，提供：
- 请求参数验证
- 响应数据序列化
- 类型提示和IDE自动补全
- 自动错误信息生成

所有API的输入输出都必须经过Schemas定义，确保前后端数据交互的一致性。

## 模型分类

| 类别 | 文件 | 职责 |
|-----|------|------|
| 基础模型 | `models.py` | 枚举类型、通用常量、基础类型定义 |
| 请求模型 | `request.py` | API输入数据模型、参数验证 |
| 响应模型 | `response.py` | API输出数据模型、序列化格式 |

## 技术选型

| 组件 | 选型 | 理由 |
|-----|------|------|
| 数据验证 | Pydantic v2 | 类型安全、自动验证、IDE友好 |
| 日期处理 | datetime | Python标准库 |
| 序列化 | Pydantic model_dump_json | 自动JSON序列化 |

## 项目中的实现

### models.py - 基础模型定义

```python
# schemas/models.py
"""
Schemas基础模型模块

定义枚举类型、通用常量、以及跨模块复用的基础类型。
"""

from enum import Enum
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime


# ==================== 枚举类型 ====================

class UserRole(str, Enum):
    """用户角色枚举"""
    ADMIN = "admin"           # 管理员
    USER = "user"             # 普通用户
    AUDITOR = "auditor"       # 审核员


class KnowledgeStatus(str, Enum):
    """知识条目状态"""
    DRAFT = "draft"           # 草稿
    PUBLISHED = "published"   # 已发布
    ARCHIVED = "archived"     # 已归档


class CaseStatus(str, Enum):
    """案例状态"""
    SUBMITTED = "submitted"    # 已提交
    REVIEWING = "reviewing"   # 审核中
    APPROVED = "approved"     # 已通过
    REJECTED = "rejected"     # 已拒绝


class AgentMode(str, Enum):
    """Agent运行模式"""
    CHAT = "chat"            # 对话模式
    RETRIEVAL = "retrieval"  # 检索模式
    DIAGNOSIS = "diagnosis"   # 诊断模式
    GUIDANCE = "guidance"    # 作业指引模式
    FULL = "full"             # 完整流程


class IntentionType(str, Enum):
    """用户意图类型"""
    QUERY_KNOWLEDGE = "query_knowledge"      # 查询知识
    TROUBLESHOOT = "troubleshoot"            # 故障排查
    SEEK_GUIDANCE = "seek_guidance"         # 寻求指导
    SUBMIT_CASE = "submit_case"              # 提交案例
    GENERAL_CHAT = "general_chat"           # 一般对话


class ImageProcessStatus(str, Enum):
    """图片处理状态"""
    PENDING = "pending"      # 待处理
    PROCESSING = "processing"  # 处理中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 失败


class TaskStatus(str, Enum):
    """异步任务状态"""
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"


# ==================== 基础响应模型 ====================

class BaseResponse(BaseModel):
    """基础响应模型"""
    success: bool = True
    message: str = "操作成功"
    code: int = 200

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "操作成功",
                "code": 200
            }
        }


class ErrorResponse(BaseResponse):
    """错误响应模型"""
    success: bool = False
    message: str = "操作失败"
    code: int = 500

    class Config:
        json_schema_extra = {
            "example": {
                "success": False,
                "message": "参数错误",
                "code": 400
            }
        }


class PaginationMeta(BaseModel):
    """分页元信息"""
    page: int = Field(default=1, ge=1, description="当前页码")
    page_size: int = Field(default=10, ge=1, le=100, description="每页数量")
    total: int = Field(default=0, description="总数")
    total_pages: int = Field(default=0, description="总页数")

    @classmethod
    def create(cls, total: int, page: int, page_size: int) -> "PaginationMeta":
        """创建分页元信息"""
        total_pages = (total + page_size - 1) // page_size
        return cls(total=total, page=page, page_size=page_size, total_pages=total_pages)


# ==================== 通用数据结构 ====================

class DetectionBox(BaseModel):
    """检测框"""
    x1: float = Field(description="左上角X坐标")
    y1: float = Field(description="左上角Y坐标")
    x2: float = Field(description="右下角X坐标")
    y2: float = Field(description="右下角Y坐标")

    def to_xyxy(self) -> List[float]:
        """转换为[x1, y1, x2, y2]格式"""
        return [self.x1, self.y1, self.x2, self.y2]


class DetectionResult(BaseModel):
    """检测结果"""
    class_name: str = Field(description="类别名称")
    confidence: float = Field(description="置信度", ge=0.0, le=1.0)
    bbox: DetectionBox = Field(description="检测框")


class VectorSearchResult(BaseModel):
    """向量搜索结果"""
    id: str = Field(description="向量ID")
    score: float = Field(description="相似度分数")
    content: str = Field(description="关联内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class GraphNode(BaseModel):
    """图谱节点"""
    id: str = Field(description="节点ID")
    label: str = Field(description="节点标签")
    properties: Dict[str, Any] = Field(default_factory=dict, description="节点属性")


class GraphRelation(BaseModel):
    """图谱关系"""
    source_id: str = Field(description="源节点ID")
    target_id: str = Field(description="目标节点ID")
    relation_type: str = Field(description="关系类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="关系属性")


class GraphQueryResult(BaseModel):
    """图谱查询结果"""
    nodes: List[GraphNode] = Field(default_factory=list)
    relations: List[GraphRelation] = Field(default_factory=list)
```

### request.py - 请求模型定义

```python
# schemas/request.py
"""
Schemas请求模型模块

定义所有API的请求数据模型，包括参数验证和默认值处理。
"""

from typing import Optional, List
from pydantic import BaseModel, Field, validator
from models import AgentMode, CaseStatus


# ==================== 对话相关 ====================

class ChatRequest(BaseModel):
    """对话请求"""
    session_id: str = Field(..., description="会话ID，用于追踪对话历史")
    message: str = Field(..., min_length=1, max_length=50000, description="用户消息")
    mode: AgentMode = Field(default=AgentMode.CHAT, description="运行模式")
    images: Optional[List[str]] = Field(default=None, description="图片URL列表")
    stream: bool = Field(default=True, description="是否启用流式输出")

    @validator('images')
    def validate_images(cls, v):
        if v and len(v) > 10:
            raise ValueError("最多支持10张图片")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "sess_abc123",
                "message": "电动机轴承过热是什么原因？",
                "mode": "diagnosis",
                "images": ["https://example.com/fault1.jpg"],
                "stream": True
            }
        }


# ==================== 知识库相关 ====================

class KnowledgeCreateRequest(BaseModel):
    """创建知识条目请求"""
    title: str = Field(..., min_length=1, max_length=255, description="标题")
    content: str = Field(..., min_length=1, description="内容")
    category: Optional[str] = Field(default=None, max_length=50, description="分类")
    tags: Optional[List[str]] = Field(default=None, description="标签列表")
    file_urls: Optional[List[str]] = Field(default=None, description="关联文件URLs")


class KnowledgeUpdateRequest(BaseModel):
    """更新知识条目请求"""
    title: Optional[str] = Field(default=None, max_length=255, description="标题")
    content: Optional[str] = Field(default=None, description="内容")
    category: Optional[str] = Field(default=None, max_length=50, description="分类")
    tags: Optional[List[str]] = Field(default=None, description="标签列表")
    status: Optional[str] = Field(default=None, description="状态")


class KnowledgeSearchRequest(BaseModel):
    """知识检索请求"""
    query: str = Field(..., min_length=1, description="查询文本")
    images: Optional[List[str]] = Field(default=None, description="查询图片")
    top_k: int = Field(default=10, ge=1, le=50, description="返回数量")
    category: Optional[str] = Field(default=None, description="分类过滤")
    tags: Optional[List[str]] = Field(default=None, description="标签过滤")


class KnowledgeUploadRequest(BaseModel):
    """知识上传请求"""
    title: str = Field(..., description="文档标题")
    file_name: str = Field(..., description="文件名")
    file_url: str = Field(..., description="文件URL")
    category: Optional[str] = Field(default=None, description="分类")


# ==================== 案例相关 ====================

class CaseCreateRequest(BaseModel):
    """创建案例请求"""
    title: str = Field(..., min_length=1, max_length=255, description="案例标题")
    description: str = Field(..., description="故障描述")
    symptom: Optional[str] = Field(default=None, description="故障现象")
    cause: Optional[str] = Field(default=None, description="故障原因")
    solution: Optional[str] = Field(default=None, description="解决方案")
    device_id: Optional[int] = Field(default=None, description="关联设备ID")
    images: Optional[List[str]] = Field(default=None, description="故障图片URLs")


class CaseUpdateRequest(BaseModel):
    """更新案例请求"""
    title: Optional[str] = Field(default=None, max_length=255, description="案例标题")
    description: Optional[str] = Field(default=None, description="故障描述")
    symptom: Optional[str] = Field(default=None, description="故障现象")
    cause: Optional[str] = Field(default=None, description="故障原因")
    solution: Optional[str] = Field(default=None, description="解决方案")
    device_id: Optional[int] = Field(default=None, description="关联设备ID")
    images: Optional[List[str]] = Field(default=None, description="故障图片URLs")


class CaseSubmitRequest(BaseModel):
    """提交案例审核请求"""
    case_id: int = Field(..., description="案例ID")
    submitter_comment: Optional[str] = Field(default=None, description="提交说明")


class CaseReviewRequest(BaseModel):
    """审核案例请求"""
    case_id: int = Field(..., description="案例ID")
    status: CaseStatus = Field(..., description="审核状态")
    review_comment: Optional[str] = Field(default=None, description="审核意见")


# ==================== 设备相关 ====================

class DeviceCreateRequest(BaseModel):
    """创建设备请求"""
    name: str = Field(..., min_length=1, max_length=100, description="设备名称")
    model: Optional[str] = Field(default=None, max_length=100, description="设备型号")
    category: Optional[str] = Field(default=None, max_length=50, description="设备类别")
    manufacturer: Optional[str] = Field(default=None, max_length=100, description="制造商")
    specs: Optional[dict] = Field(default=None, description="规格参数")


class DeviceUpdateRequest(BaseModel):
    """更新设备请求"""
    name: Optional[str] = Field(default=None, max_length=100, description="设备名称")
    model: Optional[str] = Field(default=None, max_length=100, description="设备型号")
    category: Optional[str] = Field(default=None, max_length=50, description="设备类别")
    manufacturer: Optional[str] = Field(default=None, max_length=100, description="制造商")
    specs: Optional[dict] = Field(default=None, description="规格参数")


# ==================== 图谱相关 ====================

class GraphQueryRequest(BaseModel):
    """图谱查询请求"""
    entity_name: Optional[str] = Field(default=None, description="实体名称")
    entity_type: Optional[str] = Field(default=None, description="实体类型")
    relation_type: Optional[str] = Field(default=None, description="关系类型")
    depth: int = Field(default=1, ge=1, le=3, description="查询深度")


class GraphPathRequest(BaseModel):
    """图谱路径查询请求"""
    source_name: str = Field(..., description="起点实体名称")
    target_name: str = Field(..., description="终点实体名称")
    max_hops: int = Field(default=3, ge=1, le=5, description="最大跳数")


# ==================== 工具调用相关 ====================

class YoloDetectRequest(BaseModel):
    """YOLO检测请求"""
    image_url: str = Field(..., description="图片URL")
    conf_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度阈值")


class SamSegmentRequest(BaseModel):
    """SAM分割请求"""
    image_url: str = Field(..., description="图片URL")
    bbox: Optional[List[float]] = Field(default=None, description="边界框[x1,y1,x2,y2]")
    point: Optional[List[float]] = Field(default=None, description="点击点[x,y]")


class ClipEmbedRequest(BaseModel):
    """CLIP向量化请求"""
    text: Optional[str] = Field(default=None, description="文本")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    mode: str = Field(default="text", description="模式: text/image/multimodal")

    @validator('text', 'image_url')
    def at_least_one_required(cls, v, values):
        if not values.get('text') and not values.get('image_url'):
            raise ValueError("text或image_url至少需要提供一个")
        return v


class DocumentParseRequest(BaseModel):
    """文档解析请求"""
    file_url: str = Field(..., description="文档URL")
    file_type: str = Field(default="pdf", description="文件类型，目前仅支持 pdf")
```

### response.py - 响应模型定义

```python
# schemas/response.py
"""
Schemas响应模型模块

定义所有API的响应数据模型，包括分页、错误处理等。
"""

from typing import Optional, List, Any, Generic, TypeVar
from pydantic import BaseModel, Field
from models import (
    BaseResponse, ErrorResponse, PaginationMeta,
    DetectionResult, VectorSearchResult, GraphNode, GraphRelation
)


# ==================== 对话相关 ====================

class ChatStreamEvent(BaseModel):
    """对话流式事件"""
    event: str = Field(..., description="事件类型: token/status/tool/done/error")
    data: Any = Field(..., description="事件数据")

    class Config:
        json_schema_extra = {
            "example": {
                "event": "token",
                "data": {"content": "维修"}
            }
        }


class ChatResponse(BaseResponse):
    """对话响应（非流式）"""
    session_id: str = Field(..., description="会话ID")
    message: str = Field(..., description="AI回复")
    intention: Optional[str] = Field(default=None, description="识别到的意图")
    tools_used: Optional[List[str]] = Field(default=None, description="使用的工具列表")
    latency_ms: Optional[int] = Field(default=None, description="响应延迟(ms)")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "操作成功",
                "code": 200,
                "session_id": "sess_abc123",
                "message": "电动机轴承过热可能由以下原因造成：1. 润滑不良...",
                "intention": "troubleshoot",
                "tools_used": ["knowledge_retrieval", "graph_query"],
                "latency_ms": 1500
            }
        }


# ==================== 知识库相关 ====================

class KnowledgeItem(BaseModel):
    """知识条目"""
    id: int
    title: str
    content: str
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    file_urls: Optional[List[str]] = None
    status: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class KnowledgeListResponse(BaseResponse):
    """知识列表响应"""
    data: List[KnowledgeItem]
    meta: PaginationMeta


class KnowledgeDetailResponse(BaseResponse):
    """知识详情响应"""
    data: KnowledgeItem


class KnowledgeSearchResponse(BaseResponse):
    """知识检索响应"""
    data: List[VectorSearchResult]
    total: int
    query_time_ms: int


# ==================== 案例相关 ====================

class CaseItem(BaseModel):
    """案例项"""
    id: int
    title: str
    description: str
    symptom: Optional[str] = None
    cause: Optional[str] = None
    solution: Optional[str] = None
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    images: Optional[List[str]] = None
    status: str
    submitter_id: int
    submitter_name: Optional[str] = None
    reviewer_id: Optional[int] = None
    reviewer_name: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_comment: Optional[str] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class CaseListResponse(BaseResponse):
    """案例列表响应"""
    data: List[CaseItem]
    meta: PaginationMeta


class CaseDetailResponse(BaseResponse):
    """案例详情响应"""
    data: CaseItem


# ==================== 设备相关 ====================

class DeviceItem(BaseModel):
    """设备项"""
    id: int
    name: str
    model: Optional[str] = None
    category: Optional[str] = None
    manufacturer: Optional[str] = None
    specs: Optional[dict] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class DeviceListResponse(BaseResponse):
    """设备列表响应"""
    data: List[DeviceItem]
    meta: PaginationMeta


class DeviceDetailResponse(BaseResponse):
    """设备详情响应"""
    data: DeviceItem


# ==================== 图谱相关 ====================

class GraphQueryResponse(BaseResponse):
    """图谱查询响应"""
    nodes: List[GraphNode]
    relations: List[GraphRelation]
    query_time_ms: int


class GraphPathResponse(BaseResponse):
    """图谱路径查询响应"""
    paths: List[List[GraphNode]]
    total_paths: int


class GraphStatsResponse(BaseResponse):
    """图谱统计响应"""
    total_nodes: int
    total_relations: int
    node_types: dict
    relation_types: dict


# ==================== 工具调用相关 ====================

class YoloDetectResponse(BaseResponse):
    """YOLO检测响应"""
    image_url: str
    detections: List[DetectionResult]
    process_time_ms: int


class SamSegmentResponse(BaseResponse):
    """SAM分割响应"""
    image_url: str
    masks: List[dict]
    labels: List[str]
    process_time_ms: int


class ClipEmbedResponse(BaseResponse):
    """CLIP向量化响应"""
    embedding: List[float]
    dimension: int
    model: str


class DocumentParseResponse(BaseResponse):
    """文档解析响应"""
    file_name: str
    total_pages: int
    pages: List[dict]
    tables: List[dict]
    images: List[str]
    process_time_ms: int


# ==================== 任务相关 ====================

class TaskStatusResponse(BaseResponse):
    """任务状态响应"""
    task_id: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
```

## 使用示例

### 1. 在API路由中使用

```python
# api/main.py
from fastapi import APIRouter, HTTPException
from schemas.request import ChatRequest, KnowledgeSearchRequest
from schemas.response import ChatResponse, KnowledgeSearchResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """对话接口"""
    try:
        result = await agent_service.chat(
            session_id=request.session_id,
            message=request.message,
            mode=request.mode,
            images=request.images,
            stream=False
        )
        return ChatResponse(
            success=True,
            message="操作成功",
            code=200,
            session_id=request.session_id,
            message=result["message"],
            intention=result.get("intention"),
            tools_used=result.get("tools_used"),
            latency_ms=result.get("latency_ms")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 2. 数据验证与转换

```python
from schemas.request import ChatRequest
from models import AgentMode

# 自动验证
request = ChatRequest(
    session_id="sess_123",
    message="电动机不转了",
    mode=AgentMode.DIAGNOSIS,
    images=["http://example.com/img1.jpg"]
)

# 枚举值自动转换
print(request.mode)  # AgentMode.DIAGNOSIS

# JSON序列化
json_data = request.model_dump_json()
print(json_data)
```

## Java对应实现参考

| Python Pydantic | Java对应 |
|-----------------|---------|
| `Field(..., description=)` | `@Schema(description=)` |
| `Field(default=10, ge=1)` | `@Min(1) @Max(100)` |
| `validator` | `@Valid` + 自定义验证器 |
| `BaseModel` | Lombok `@Data` + Jakarta Validation |

## 文件结构

```
schemas/
├── __init__.py
├── README.md                    # 本文件
├── models.py                    # 基础模型、枚举
├── request.py                   # 请求模型
└── response.py                  # 响应模型
```
