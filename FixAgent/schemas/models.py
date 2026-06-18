"""
Schemas基础模型模块

定义枚举类型、通用常量、以及跨模块复用的基础类型。

【关联功能】
- agents/: Agent运行模式选择、意图识别
- api/: HTTP请求参数校验、响应格式统一

【使用顺序】
1. 请求进入 API 时，先用枚举校验 mode、status 等字段
2. Agent/Chain 内部用枚举做流程分支判断
3. 响应时枚举值会序列化到 JSON 返回

【枚举用途速查】
| 枚举 | 用途 | 关键场景 |
|-----|------|---------|
| UserRole | 用户权限 | 案例审核时判断审核员权限 |
| KnowledgeStatus | 知识状态 | 知识库的发布/归档管理 |
| CaseStatus | 案例状态 | 案例提交→审核→通过/拒绝流程 |
| AgentMode | Agent模式 | 调度时选择 retrieval/diagnosis/guidance |
| TaskStatus | 异步任务 | 长任务的状态查询 |
"""

from enum import Enum
from typing import List, Any, Dict
from pydantic import BaseModel, Field, ConfigDict


# ==================== 枚举类型 ====================

class UserRole(str, Enum):
    """
    用户角色枚举

    【关联】Java后端的用户权限系统
    【何时用】案例审核时，校验操作人是否为 AUDITOR 角色

    【使用顺序】
    1. Java 后端在调用审核接口前，校验当前用户角色
    2. Python API 收到请求时，从请求头/上下文获取角色信息
    3. 仅影响审核类操作（CaseReviewRequest）

    【值说明】
    - ADMIN: 系统管理员，可管理所有资源
    - USER: 普通用户，可提交案例、查询知识
    - AUDITOR: 审核员，可审核他人提交的案例
    """
    ADMIN = "admin"           # 管理员
    USER = "user"             # 普通用户
    AUDITOR = "auditor"       # 审核员


class KnowledgeStatus(str, Enum):
    """
    知识条目状态枚举

    【关联】知识库管理、检索过滤
    【何时用】
    - 创建知识时默认 DRAFT
    - 发布知识时改为 PUBLISHED
    - 归档知识时改为 ARCHIVED
    - 检索时可过滤特定状态

    【使用顺序】
    1. 知识创建 → DRAFT（草稿）
    2. 知识发布审核 → PUBLISHED（已发布，可检索）
    3. 知识废弃 → ARCHIVED（归档，不再展示）

    【Java对应】KnowledgeStatusEnum
    """
    DRAFT = "draft"           # 草稿（不可检索）
    PUBLISHED = "published"   # 已发布（可检索）
    ARCHIVED = "archived"     # 已归档（保留但隐藏）


class CaseStatus(str, Enum):
    """
    案例状态枚举

    【关联】案例提交与审核流程
    【何时用】案例从提交到审核的全生命周期

    【使用顺序】
    1. 用户提交案例 → SUBMITTED
    2. 审核员开始审核 → REVIEWING
    3. 审核通过 → APPROVED（进入案例库）
    4. 审核拒绝 → REJECTED（可修改后重新提交）

    【Java对应】CaseStatusEnum
    """
    SUBMITTED = "submitted"    # 已提交（待审核）
    REVIEWING = "reviewing"   # 审核中
    APPROVED = "approved"     # 已通过
    REJECTED = "rejected"     # 已拒绝


class AgentMode(str, Enum):
    """
    Agent运行模式枚举

    注意：此枚举属于旧架构（Orchestrator + 子Agent），
    当前已由 FixAgent 统一 ReAct 循环替代。
    保留仅用于兼容 Java 端旧请求，新代码不应依赖此枚举。

    旧架构中各模式的说明：
    - CHAT: 一般对话，不调用工具
    - RETRIEVAL: 纯检索，从向量库查知识
    - DIAGNOSIS: 纯诊断，分析故障原因
    - GUIDANCE: 纯指引，生成维修步骤
    - FULL: 完整流程（检索→诊断→指引）

    当前 FixAgent 完全忽略此字段，通过 ReAct 循环自主决定行为。
    """
    CHAT = "chat"            # 对话模式（简单问答）
    RETRIEVAL = "retrieval"  # 检索模式（查知识库）
    DIAGNOSIS = "diagnosis"   # 诊断模式（故障分析）
    GUIDANCE = "guidance"    # 指引模式（生成步骤）
    FULL = "full"             # 完整流程（检索+诊断+指引）


class TaskStatus(str, Enum):
    """
    异步任务状态枚举

    【关联】长任务执行、Celery/后台任务
    【何时用】需要异步执行时的状态查询

    【使用顺序】
    1. 任务创建 → PENDING
    2. 任务开始执行 → STARTED
    3. 执行成功 → SUCCESS（可获取结果）
    4. 执行失败 → FAILURE（可获取错误信息）

    【对应响应】TaskStatusResponse
    【常见场景】批量文档解析、大规模知识导入
    """
    PENDING = "pending"    # 等待执行
    STARTED = "started"   # 开始执行
    SUCCESS = "success"   # 执行成功
    FAILURE = "failure"   # 执行失败

# ==================== 基础响应模型 ====================

class BaseResponse(BaseModel):
    """
    基础响应模型

    【功能关联】所有 API 响应的基类，统一响应格式
    【何时用】每个 HTTP 响应体的最外层结构
    【使用顺序】
    1. API 端点返回时，外层一定是 BaseResponse（或其子类）
    2. 子类继承扩展特定业务字段
    3. Java 端统一按 BaseResponse 解析

    【字段说明】
    - success: 操作是否成功，Java 可据此判断业务处理结果
    - message: 友好提示信息，用于展示给用户
    - code: HTTP 状态码（200成功，4xx客户端错误，5xx服务端错误）

    【JSON 示例】
    ```json
    {
        "success": true,
        "message": "操作成功",
        "code": 200
    }
    ```

    【继承关系】
    - ChatResponse → BaseResponse（扩展 session_id, message, intention 等）
    - KnowledgeListResponse → BaseResponse（扩展 data, meta）
    - ErrorResponse → BaseResponse（重置 success=False）
    """
    success: bool = True
    message: str = "操作成功"
    code: int = 200

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "success": True,
            "message": "操作成功",
            "code": 200
        }
    })


class ErrorResponse(BaseResponse):
    """
    错误响应模型

    【功能关联】API 异常处理、全局错误捕获
    【何时用】
    - HTTP 500 系统错误
    - 业务校验失败（如参数错误、无权限）
    - 外部服务调用失败（向量库、图谱服务）

    【使用顺序】
    1. API 捕获异常 → @app.exception_handler
    2. 构建 ErrorResponse → success=False
    3. 返回给 Java 端统一处理

    【与 BaseResponse 的区别】
    - BaseResponse.success = True（成功响应）
    - ErrorResponse.success = False（固定为失败）
    - ErrorResponse.message 应说明失败原因

    【Java 端处理示例】
    ```java
    if (!response.isSuccess()) {
        // 显示错误提示
        showToast(response.getMessage());
    }
    ```

    【code 对应关系】
    - 400: 参数错误、校验失败
    - 401: 未认证
    - 403: 无权限
    - 404: 资源不存在
    - 500: 系统内部错误
    """
    success: bool = False
    message: str = "操作失败"
    code: int = 500

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "success": False,
            "message": "参数错误",
            "code": 400
        }
    })


class PaginationMeta(BaseModel):
    """
    分页元信息模型

    【功能关联】列表查询接口的分页信息
    【何时用】知识列表、案例列表、设备列表等需要分页的响应

    【使用顺序】
    1. 前端发起列表请求 → 传入 page, page_size
    2. 后端查询数据库/向量库
    3. 返回列表数据 + PaginationMeta（包含总数、页码等）

    【字段说明】
    - page: 当前页码（从1开始）
    - page_size: 每页条数（默认10，最大100）
    - total: 总记录数
    - total_pages: 总页数（计算得出）

    【Java 端使用示例】
    ```java
    PaginationMeta meta = response.getMeta();
    int totalPages = meta.getTotalPages();
    boolean hasNext = meta.getPage() < meta.getTotalPages();
    ```

    【前端分页逻辑】
    ```
    总页数 = ceil(total / page_size)
    是否有下一页 = page < total_pages
    偏移量 = (page - 1) * page_size
    ```
    """
    page: int = Field(default=1, ge=1, description="当前页码")
    page_size: int = Field(default=10, ge=1, le=100, description="每页数量")
    total: int = Field(default=0, description="总数")
    total_pages: int = Field(default=0, description="总页数")

    @classmethod
    def create(cls, total: int, page: int, page_size: int) -> "PaginationMeta":
        """
        创建分页元信息

        【用法】在 API 端点中替代手动构造
        ```python
        # 之前
        return KnowledgeListResponse(
            data=items,
            meta=PaginationMeta(total=count, page=page, page_size=page_size, total_pages=pages)
        )

        # 之后
        return KnowledgeListResponse(
            data=items,
            meta=PaginationMeta.create(total=count, page=page, page_size=page_size)
        )
        ```
        """
        total_pages = (total + page_size - 1) // page_size
        return cls(total=total, page=page, page_size=page_size, total_pages=total_pages)


# ==================== 通用数据结构 ====================

class VectorSearchResult(BaseModel):
    """
    向量搜索结果模型

    【功能关联】Redis 向量库检索、知识搜索
    【何时用】KnowledgeSearchResponse 中返回单条检索结果

    【字段说明】
    - id: 知识条目ID（对应 Redis 中的 key 或数据库主键）
    - score: 相似度分数（0.0~1.0，越高越相似）
    - content: 知识内容原文
    - metadata: 附加信息（如来源、更新时间等）

    【使用顺序】
    1. 用户查询 → 生成向量
    2. Redis ANN 检索 → 返回 VectorSearchResult 列表
    3. 按 score 排序展示给用户

    【score 阈值参考】
    - >= 0.9: 高度相关
    - 0.7~0.9: 中度相关
    - 0.5~0.7: 低度相关（可参考）
    - < 0.5: 不相关（建议过滤）

    【metadata 常见字段】
    ```json
    {
        "source": "检修手册第三章",
        "category": "motor",
        "tags": ["轴承", "过热"],
        "updated_at": "2024-01-15"
    }
    ```
    """
    id: str = Field(description="向量ID")
    score: float = Field(description="相似度分数")
    content: str = Field(description="关联内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    raw_score: float | None = Field(default=None, description="底层检索原始分数")
    raw_score_type: str | None = Field(default=None, description="底层分数类型")
    relevance_score: float | None = Field(default=None, description="统一相关度分数，越大越相关")
    retrieval_route: str | None = Field(default=None, description="召回路线")
    rerank_score: float | None = Field(default=None, description="重排分数")


class GraphNode(BaseModel):
    """
    图谱节点模型

    【功能关联】Neo4j 图数据库、知识图谱查询
    【何时用】GraphQueryResponse 中描述图谱中的一个实体

    【字段说明】
    - id: 节点唯一标识（Neo4j 内部 ID 或业务 ID）
    - label: 节点类型标签（如 "Symptom"、"Device"、"Cause"）
    - properties: 节点属性字典

    【使用顺序】
    1. 调用图谱查询（GraphQueryRequest）
    2. 返回节点列表 List[GraphNode]
    3. 前端渲染图谱可视化

    【label 类型示例】
    | label | 含义 | 示例 |
    |-------|------|------|
    | Symptom | 故障现象 | "轴承过热"、"异响" |
    | Device | 设备 | "电动机"、"泵" |
    | Cause | 故障原因 | "润滑不良"、"轴承磨损" |
    | Solution | 解决方案 | "更换轴承"、"添加润滑油" |

    【properties 示例】
    ```json
    {
        "name": "轴承过热",
        "severity": "high",
        "description": "电动机轴承温度超过额定值"
    }
    ```
    """
    id: str = Field(description="节点ID")
    label: str = Field(description="节点标签")
    properties: Dict[str, Any] = Field(default_factory=dict, description="节点属性")


class GraphRelation(BaseModel):
    """
    图谱关系模型

    【功能关联】Neo4j 图数据库、知识图谱查询
    【何时用】GraphQueryResponse 中描述图谱中两个节点的关系

    【字段说明】
    - source_id: 源节点ID
    - target_id: 目标节点ID
    - relation_type: 关系类型（如 "causes"、"belongs_to"）
    - properties: 关系属性

    【使用顺序】
    1. 图谱查询返回节点和关系
    2. 前端根据 source_id 和 target_id 绘制连线
    3. 关系类型影响边的样式和颜色

    【relation_type 类型示例】
    | relation_type | 含义 | 示例 |
    |--------------|------|------|
    | causes | 导致 | 轴承磨损 → 异响 |
    | belongs_to | 属于 | 轴承 → 电动机 |
    | requires | 需要 | 更换轴承 → 需要工具 |
    | similar_to | 相似 | 轴承过热 → 轴承振动 |

    【properties 示例】
    ```json
    {
        "weight": 0.8,
        "description": "统计数据显示该原因占比80%"
    }
    ```
    """
    source_id: str = Field(description="源节点ID")
    target_id: str = Field(description="目标节点ID")
    relation_type: str = Field(description="关系类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="关系属性")


class GraphQueryResult(BaseModel):
    """
    图谱查询结果模型（内部使用）

    【功能关联】图谱服务返回结果的封装
    【何时用】
    - GraphQueryTool.execute() 返回结果

    【注意】此模型目前未被 GraphQueryResponse 直接使用
    GraphQueryResponse 直接使用了 nodes 和 relations 字段
    此模型可能是为未来统一封装准备的

    【与 GraphQueryResponse 的区别】
    - GraphQueryResult: 更纯粹的图谱数据包装
    - GraphQueryResponse: 继承 BaseResponse，带 success/code 等

    【建议】如无特殊需求，可考虑让 GraphQueryResponse 继承此模型
    """
    nodes: List[GraphNode] = Field(default_factory=list)
    relations: List[GraphRelation] = Field(default_factory=list)


