"""
Schemas请求模型模块

定义所有API的请求数据模型，包括参数验证和默认值处理。

【模块职责】
- 接收 Java 后端传来的请求数据
- 校验参数合法性（类型、长度、范围）
- 提供清晰的错误提示

【重要：模型分类说明】
本文件中的模型分为三类：
1. 已实现 API 端点 → Python 端有对应的 FastAPI 路由（如 ChatRequest, KnowledgeImportRequest）
2. Java 端专用 → 仅供 Java 后端参考数据结构，Python 端不提供服务端点的
   （如 GraphQueryRequest, CaseCreateRequest, DeviceCreateRequest 等）
标注在对应类的注释中。新开发时注意区分。

【使用顺序】
1. Java 端构造请求 JSON
2. FastAPI 自动反序列化 → Pydantic 模型
3. 校验不通过 → 422 Unprocessable Entity
4. 校验通过 → 传递给 Agent/Service 处理

【与 response.py 的对应关系】
- request.py: 请求参数（输入）
- response.py: 响应数据（输出）
- 一个请求通常对应一个响应（如 ChatRequest → ChatResponse）

【Java 端对接示例】
```java
ChatRequest request = ChatRequest.builder()
    .sessionId("sess_abc123")
    .message("电动机轴承过热是什么原因？")
    .mode(AgentMode.DIAGNOSIS)
    .images(List.of("https://cdn.example.com/fault.jpg"))
    .stream(false)
    .build();
```

【校验规则优先级】
1. 必填字段校验（... 或 Field(required=True)）
2. 类型校验（str/int/List）
3. 长度校验（min_length/max_length）
4. 范围校验（ge/le 用于数字）
5. 自定义校验（@validator）
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from schemas.models import AgentMode, CaseStatus


# ==================== 对话相关 ====================

class ChatRequest(BaseModel):
    """
    对话请求模型

    【功能关联】/ai/chat、/ai/retrieval、/ai/diagnosis、/ai/guidance、/ai/pipeline
    【何时用】用户发起对话请求时使用

    【使用顺序】
    1. Java 后端从 Redis 获取 session_id
    2. 组装 ChatRequest（包含用户消息、会话ID、模式）
    3. 调用 Python AI 服务
    4. 返回 ChatResponse

    【字段说明】
    - session_id: Java 生成，用于追踪对话历史和日志
    - message: 用户输入（1~2000字符）
    - mode: Agent 运行模式（默认 CHAT）
    - images: 可选的故障图片 URL 列表（最多10张）
    - stream: 是否流式输出（默认 True）

    【mode 与接口的对应关系】
    | mode | 调用接口 | Agent 行为 |
    |-----|---------|-----------|
    | CHAT | /ai/chat | 简单对话，不调用工具 |
    | RETRIEVAL | /ai/chat | 检索知识库 |
    | DIAGNOSIS | /ai/chat | 故障诊断 |
    | GUIDANCE | /ai/chat | 生成维修指引 |
    | FULL | /ai/pipeline | 完整流程 |

    【images 使用场景】
    - 故障图片：用户上传设备故障照片
    - 维修前后对比：用于判断维修效果
    - 最多10张：防止单次请求过大

    【Java 对应类】
    ```java
    public class ChatRequest {
        String sessionId;
        String message;
        AgentMode mode;  // 枚举: CHAT, RETRIEVAL, DIAGNOSIS, GUIDANCE, FULL
        List<String> images;
        boolean stream;
    }
    ```
    """
    session_id: str = Field(..., description="会话ID，用于追踪对话历史")
    message: str = Field(default="", max_length=50000, description="用户消息（当前轮纯文本，可在仅上传图片时为空）")
    mode: AgentMode = Field(default=AgentMode.CHAT, description="运行模式")
    images: Optional[List[str]] = Field(default=None, description="图片URL列表")
    stream: bool = Field(default=True, description="是否启用流式输出")
    conversation_history: Optional[List[dict]] = Field(default=None, description="多轮对话历史，格式：[{'role':'user','content':'...'},{'role':'assistant','content':'...'}]")
    context: Optional[dict] = Field(default=None, description="结构化上下文（摘要、事实、偏好、待办）")
    device_type: Optional[str] = Field(default=None, description="检索范围限定的设备型号（会话绑定，缺省=不限定，退回全库检索）")
    document_id: Optional[str] = Field(default=None, description="检索范围限定的单本手册ID（可选，比 device_type 更严）")

    @field_validator('images')
    @classmethod
    def validate_images(cls, v):
        """校验图片数量不超过10张"""
        if v and len(v) > 10:
            raise ValueError("最多支持10张图片")
        return v

    @model_validator(mode="after")
    def validate_message_or_images(self):
        """允许纯图片提问，但不允许文本和图片同时为空。"""
        if not (self.message or "").strip() and not self.images:
            raise ValueError("message 和 images 不能同时为空")
        return self

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "session_id": "sess_abc123",
            "message": "电动机轴承过热是什么原因？",
            "mode": "diagnosis",
            "images": ["https://example.com/fault1.jpg"],
            "stream": True
        }
    })


# ==================== 知识库相关 ====================

class KnowledgeCreateRequest(BaseModel):
    """
    创建知识条目请求

    【功能关联】知识库管理、文档导入
    【何时用】向知识库新增一条知识时

    【使用顺序】
    1. 用户在 Java 后台填写知识信息
    2. 调用 Python API 创建知识
    3. 知识存入向量库（Redis）和关系库（可选 Neo4j）
    4. 返回创建结果

    【字段说明】
    - title: 知识标题（1~255字符）
    - content: 知识正文（支持长文本）
    - category: 分类（如 "motor"、"pump"，可选）
    - tags: 标签列表（用于过滤检索）
    - file_urls: 关联的文件 URL（如 PDF、图片）

    【Java 对应类】
    ```java
    public class KnowledgeCreateRequest {
        String title;
        String content;
        String category;
        List<String> tags;
        List<String> fileUrls;
    }
    ```
    """
    title: str = Field(..., min_length=1, max_length=255, description="标题")
    content: str = Field(..., min_length=1, description="内容")
    category: Optional[str] = Field(default=None, max_length=50, description="分类")
    tags: Optional[List[str]] = Field(default=None, description="标签列表")
    file_urls: Optional[List[str]] = Field(default=None, description="关联文件URLs")


class KnowledgeUpdateRequest(BaseModel):
    """
    更新知识条目请求

    【功能关联】知识库管理、知识编辑
    【何时用】修改已有知识的内容时

    【使用顺序】
    1. 用户编辑已有知识
    2. 调用更新接口
    3. 向量库同步更新
    4. 返回更新结果

    【与 KnowledgeCreateRequest 的区别】
    - Create: 所有字段必填
    - Update: 字段都是可选的，只更新传入的字段

    【status 字段说明】
    - 可选值: "draft", "published", "archived"
    - 来自 KnowledgeStatus 枚举
    """
    title: Optional[str] = Field(default=None, max_length=255, description="标题")
    content: Optional[str] = Field(default=None, description="内容")
    category: Optional[str] = Field(default=None, max_length=50, description="分类")
    tags: Optional[List[str]] = Field(default=None, description="标签列表")
    status: Optional[str] = Field(default=None, description="状态")


class KnowledgeSearchRequest(BaseModel):
    """
    知识检索请求

    【功能关联】/ai/knowledge/search 端点
    【何时用】用户搜索知识库时

    【使用顺序】
    1. 用户输入查询文本（和/或图片）
    2. 生成查询向量
    3. Redis ANN 检索
    4. 返回 top_k 条最相关结果

    【字段说明】
    - query: 文本查询（支持自然语言）
    - images: 图片查询（多模态检索）
    - top_k: 返回数量（1~50，默认10）
    - category: 按分类过滤（可选）
    - tags: 按标签过滤（可选）

    【检索逻辑】
    - 纯文本: CLIP text encoder → 向量 → ANN 检索
    - 纯图片: CLIP image encoder → 向量 → ANN 检索
    - 图文混合: 拼接两个向量 → 检索

    【Java 对应类】
    ```java
    public class KnowledgeSearchRequest {
        String query;
        List<String> images;
        int topK;       // 默认10
        String category;
        List<String> tags;
    }
    ```
    """
    query: str = Field(..., min_length=1, description="查询文本")
    images: Optional[List[str]] = Field(default=None, description="查询图片")
    top_k: int = Field(default=10, ge=1, le=50, description="返回数量")
    category: Optional[str] = Field(default=None, description="分类过滤")
    tags: Optional[List[str]] = Field(default=None, description="标签过滤")
    document_id: Optional[str] = Field(default=None, description="文档 ID 过滤")
    chunk_type: Optional[str] = Field(default=None, description="text/table/image/image_summary 过滤")
    device_type: Optional[str] = Field(default=None, description="设备类型过滤")
    document_version: Optional[str] = Field(default=None, description="文档版本过滤")
    manual_type: Optional[str] = Field(default=None, description="手册类型过滤")


class KnowledgeImportRequest(BaseModel):
    """
    知识导入请求（文档解析 + 向量化入库）

    【功能关联】POST /ai/knowledge/import
    【何时用】Java 后端上传维修手册 PDF 等文档，一键解析并入库

    【使用顺序】
    1. Java 后端在部署初始化时拿到赛题 PDF
    2. 调用本接口，传入文件路径/URL
    3. Python 端解析 PDF → 向量化 → 存入 Redis 向量库
    4. 返回导入统计

    【处理流程】
    编排完整的 解析→向量化→入库 管道
    """
    file_url: str = Field(..., description="文档路径或URL")
    file_type: str = Field(default="pdf", description="文件类型，目前仅支持 pdf")
    category: Optional[str] = Field(default=None, description="全局分类标签，覆盖章节自动分类")
    tags: Optional[List[str]] = Field(default=None, description="标签列表，用于过滤检索")
    document_id: Optional[str] = Field(default=None, description="业务文档 ID，不传则自动生成")
    device_type: Optional[str] = Field(default=None, description="设备类型")
    manual_type: Optional[str] = Field(default=None, description="手册类型")
    document_version: Optional[str] = Field(default=None, description="文档版本")
    replace_existing: bool = Field(default=False, description="同文档 ID 时先删除旧向量")


# ==================== 临时计划草稿相关 ====================

class TemporaryPlanGenerateRequest(BaseModel):
    """未匹配标准流程时，由 Java 请求生成的待审核临时计划草稿。"""

    request_id: str = Field(..., min_length=1, description="Java 侧生成的幂等请求 ID")
    device_type: str = Field(..., min_length=1, description="设备类型")
    maintenance_level: Optional[str] = Field(default=None, description="检修等级")
    fault_description: str = Field(..., min_length=1, description="故障描述")
    images: Optional[List[str]] = Field(default=None, description="故障图片 URL 或 data URI")
    top_k: int = Field(default=5, ge=1, le=10, description="用于生成草稿的证据数")


# ==================== 案例相关 ====================

class CaseCreateRequest(BaseModel):
    """
    创建案例请求

    【状态】Java 端专用，Python 端不提供服务端点。
    案例管理全部由 Java 后端负责，此模型仅供 Java 端参考请求数据结构。

    【使用顺序】
    1. 用户填写案例信息（故障描述、原因、解决方案）
    2. 提交案例 → CaseStatus = SUBMITTED
    3. 审核员审核 → CaseStatus = REVIEWING
    4. 审核通过/拒绝 → CaseStatus = APPROVED/REJECTED

    【字段说明】
    - title: 案例标题（简明扼要）
    - description: 详细故障描述
    - symptom: 故障现象（现象而非原因）
    - cause: 故障原因（分析得出）
    - solution: 解决方案
    - device_id: 关联的设备 ID
    - images: 故障图片列表

    【案例生命周期】
    ```
    创建(SUBMITTED) → 审核中(REVIEWING) → 通过(APPROVED)
                                   → 拒绝(REJECTED) → 修改后重新提交
    ```

    【Java 对应类】
    ```java
    public class CaseCreateRequest {
        String title;
        String description;
        String symptom;
        String cause;
        String solution;
        Integer deviceId;
        List<String> images;
    }
    ```
    """
    title: str = Field(..., min_length=1, max_length=255, description="案例标题")
    description: str = Field(..., description="故障描述")
    symptom: Optional[str] = Field(default=None, description="故障现象")
    cause: Optional[str] = Field(default=None, description="故障原因")
    solution: Optional[str] = Field(default=None, description="解决方案")
    device_id: Optional[int] = Field(default=None, description="关联设备ID")
    images: Optional[List[str]] = Field(default=None, description="故障图片URLs")


class CaseUpdateRequest(BaseModel):
    """
    更新案例请求

    【状态】Java 端专用。案例管理由 Java 后端负责。
    【何时用】修改已有案例内容时

    【注意】
    - 审核被拒绝后，案例状态会回到可编辑状态
    - 已通过的案例通常不允许修改（或需要重新审核）

    【与 CaseCreateRequest 的区别】
    - Create: 创建新案例，所有字段必填
    - Update: 编辑已有案例，字段都是可选的
    """
    title: Optional[str] = Field(default=None, max_length=255, description="案例标题")
    description: Optional[str] = Field(default=None, description="故障描述")
    symptom: Optional[str] = Field(default=None, description="故障现象")
    cause: Optional[str] = Field(default=None, description="故障原因")
    solution: Optional[str] = Field(default=None, description="解决方案")
    device_id: Optional[int] = Field(default=None, description="关联设备ID")
    images: Optional[List[str]] = Field(default=None, description="故障图片URLs")


class CaseSubmitRequest(BaseModel):
    """
    提交案例审核请求

    【状态】Java 端专用。案例审核流程由 Java 后端负责。
    【何时用】案例创建或修改完成后，提交给审核员审核

    【使用顺序】
    1. 用户创建/编辑案例
    2. 点击"提交审核"
    3. CaseStatus: SUBMITTED
    4. 审核员收到通知

    【字段说明】
    - case_id: 要提交的案例 ID
    - submitter_comment: 提交时的说明（如修改了什么）

    【Java 对应类】
    ```java
    public class CaseSubmitRequest {
        Integer caseId;
        String submitterComment;
    }
    ```
    """
    case_id: int = Field(..., description="案例ID")
    submitter_comment: Optional[str] = Field(default=None, description="提交说明")


class CaseReviewRequest(BaseModel):
    """
    审核案例请求

    【状态】Java 端专用。案例审核由 Java 后端负责。
    【何时用】审核员审核案例时使用

    【使用顺序】
    1. 审核员登录系统
    2. 查看待审核案例列表
    3. 审核案例 → CaseStatus: APPROVED 或 REJECTED
    4. 填写审核意见

    【status 可选值】
    - CaseStatus.APPROVED: 审核通过
    - CaseStatus.REJECTED: 审核拒绝

    【审核拒绝后的处理】
    - 用户修改案例内容
    - 重新提交审核

    【Java 对应类】
    ```java
    public class CaseReviewRequest {
        Integer caseId;
        CaseStatus status;     // APPROVED 或 REJECTED
        String reviewComment;
    }
    ```
    """
    case_id: int = Field(..., description="案例ID")
    status: CaseStatus = Field(..., description="审核状态")
    review_comment: Optional[str] = Field(default=None, description="审核意见")


# ==================== 设备相关 ====================

class DeviceCreateRequest(BaseModel):
    """
    创建设备请求

    【状态】Java 端专用。设备管理由 Java 后端负责，Python 端不提供服务端点。
    【何时用】向系统添加新设备时

    【使用顺序】
    1. 管理员录入新设备信息
    2. 调用创建接口
    3. 设备数据存入数据库
    4. 可关联到案例作为外键

    【字段说明】
    - name: 设备名称（如 "1号电动机"）
    - model: 设备型号
    - category: 设备类别（如 "电动机"、"泵"）
    - manufacturer: 制造商
    - specs: 规格参数字典（如 {"power": "100kW", "voltage": "380V"}）

    【Java 对应类】
    ```java
    public class DeviceCreateRequest {
        String name;
        String model;
        String category;
        String manufacturer;
        Map<String, Object> specs;
    }
    ```
    """
    name: str = Field(..., min_length=1, max_length=100, description="设备名称")
    model: Optional[str] = Field(default=None, max_length=100, description="设备型号")
    category: Optional[str] = Field(default=None, max_length=50, description="设备类别")
    manufacturer: Optional[str] = Field(default=None, max_length=100, description="制造商")
    specs: Optional[dict] = Field(default=None, description="规格参数")


class DeviceUpdateRequest(BaseModel):
    """
    更新设备请求

    【状态】Java 端专用。设备管理由 Java 后端负责。
    【何时用】修改已有设备信息时

    【与 DeviceCreateRequest 的区别】
    - Create: 所有字段必填
    - Update: 字段都是可选的
    """
    name: Optional[str] = Field(default=None, max_length=100, description="设备名称")
    model: Optional[str] = Field(default=None, max_length=100, description="设备型号")
    category: Optional[str] = Field(default=None, max_length=50, description="设备类别")
    manufacturer: Optional[str] = Field(default=None, max_length=100, description="制造商")
    specs: Optional[dict] = Field(default=None, description="规格参数")


# ==================== 图谱相关 ====================

class GraphQueryRequest(BaseModel):
    """
    图谱查询请求

    【状态】Java 端专用，Python 端不提供服务端点
    Python 端通过 Tool 方式（graph_query_tool.py）内部调用，不对外暴露 HTTP 接口。
    此模型仅供 Java 端参考请求数据结构。

    【功能关联】Neo4j 图数据库、知识图谱查询
    【何时用】查询设备故障知识图谱时

    【使用顺序】
    1. 用户描述故障现象
    2. 传入 entity_name（如 "轴承过热"）
    3. Neo4j 查询相关节点和关系
    4. 返回 GraphQueryResponse

    【字段说明】
    - entity_name: 要查询的实体名称（如故障现象、部件名称）
    - entity_type: 实体类型过滤（如 "Symptom"、"Device"）
    - relation_type: 关系类型过滤（如 "causes"、"belongs_to"）
    - depth: 查询深度（1~3，默认1）
      - depth=1: 直接关联
      - depth=2: 关联的关联
      - depth=3: 更广范围

    【查询示例】
    ```
    entity_name="轴承过热", depth=2
    →
    轴承过热 → causes → 润滑不良
                ↑          → requires → 润滑油
    润滑不良 → causes → 轴承磨损
    ```

    【Java 对应类】
    ```java
    public class GraphQueryRequest {
        String entityName;
        String entityType;
        String relationType;
        int depth;  // 默认1
    }
    ```
    """
    entity_name: Optional[str] = Field(default=None, description="实体名称")
    entity_type: Optional[str] = Field(default=None, description="实体类型")
    relation_type: Optional[str] = Field(default=None, description="关系类型")
    depth: int = Field(default=1, ge=1, le=3, description="查询深度")


class GraphPathRequest(BaseModel):
    """
    图谱路径查询请求

    【状态】Java 端专用，Python 端不提供服务端点

    【功能关联】Neo4j 图数据库、故障传播路径分析
    【何时用】查询两个实体之间的关联路径时

    【使用场景】
    - 从故障现象追溯到根本原因
    - 分析故障传播链
    - 查找设备间的关联关系

    【字段说明】
    - source_name: 起点实体名称（如 "轴承磨损"）
    - target_name: 终点实体名称（如 "设备停机"）
    - max_hops: 最大跳数（1~5，默认3）
      - hops=1: 直接相连
      - hops=2: 中间隔一个节点
      - hops=3: 隔两个节点

    【路径查询示例】
    ```
    source="轴承磨损", target="设备停机", max_hops=3

    可能路径:
    轴承磨损 → 振动过大 → 设备停机
    轴承磨损 → 润滑不良 → 温度过高 → 设备停机
    ```

    【Java 对应类】
    ```java
    public class GraphPathRequest {
        String sourceName;
        String targetName;
        int maxHops;  // 默认3
    }
    ```
    """
    source_name: str = Field(..., description="起点实体名称")
    target_name: str = Field(..., description="终点实体名称")
    max_hops: int = Field(default=3, ge=1, le=5, description="最大跳数")


# ==================== 工具调用相关 ====================

class DocumentParseRequest(BaseModel):
    """
    文档解析请求

    【状态】内部使用，由 KnowledgeService 编排调用，不对外暴露 HTTP 端点。
    调用方直接通过 document_tool.py 使用。

    【功能关联】文档解析服务（PDF、Word、TXT）
    【何时用】从文档中提取结构化知识时

    【使用顺序】
    1. 用户上传文档
    2. 调用解析接口
    3. 解析返回：页面内容、表格、图片列表
    4. 后续可存入知识库

    【字段说明】
    - file_url: 文档的 URL
    - file_type: 文档类型
      - "pdf": PDF 文档
      - "docx": Word 文档
      - "txt": 纯文本

    【返回值说明】
    - file_name: 文件名
    - total_pages: 总页数
    - pages: 每页内容列表
    - tables: 提取的表格列表
    - images: 文档中的图片列表
    - process_time_ms: 处理耗时

    【Java 对应类】
    ```java
    public class DocumentParseRequest {
        String fileUrl;
        String fileType;  // "pdf" / "docx" / "txt"
    }
    ```
    """
    file_url: str = Field(..., description="文档URL")
    file_type: str = Field(..., description="文件类型: pdf/docx/txt")


# ==================== 记忆整理相关 ====================

class MemoryMessage(BaseModel):
    """
    记忆消息

    【功能关联】记忆整理
    【对应 Java】ai.weixiu.entity.MemoryMessage
    """
    role: str = Field(..., description="角色: user/assistant")
    content: str = Field(..., description="消息内容")


class MemoryPreferenceVO(BaseModel):
    """
    偏好记忆

    【功能关联】记忆整理
    【对应 Java】ai.weixiu.pojo.vo.MemoryPreferenceVO
    """
    content: str = Field(..., description="偏好描述")
    category: str = Field(..., description="分类: 交互风格|格式要求|工作习惯|关注领域|其他")
    preferenceCategory: int = Field(..., description="偏好类型: 0=用户级(所有对话公用), 1=会话级(单次会话公用)")


class MemoryUnresolvedVO(BaseModel):
    """
    未完成摘要

    【功能关联】记忆整理
    【对应 Java】ai.weixiu.pojo.vo.MemoryUnresolvedVO

    新增 id 字段：数据库主键，用于让LLM通过ID精确标记哪些事项已解决，
    避免之前用content文本匹配导致的不精确问题。
    """
    id: Optional[int] = Field(default=None, description="数据库主键ID，用于精确标记已解决事项")
    content: str = Field(..., description="未完成任务摘要描述")
    type: str = Field(..., description="类型: 未答复回答|进行中任务|用户代办")
    status: str = Field(..., description="状态: active=进行中, superseded=已放弃")


class MemoryConsolidateRequest(BaseModel):
    """
    记忆整理请求

    【功能关联】POST /ai/memory/consolidate
    【何时用】对话达到阈值（如30条）时，Java 端调用此接口压缩对话为摘要

    【使用顺序】
    1. Java 端检测到某会话对话数 >= 阈值
    2. 从数据库取出该会话的全部对话 + 上一轮摘要
    3. 组装 MemoryIntegrationParametersVO（含 previousSummary）
    4. 调用 Python AI 服务生成渐进式摘要
    5. Java 端存储摘要和提取的记忆

    【Java 对应类】ai.weixiu.pojo.vo.MemoryIntegrationParametersVO

    新增 previousSummary 字段：上一轮整合产出的摘要，
    让 Python 端能在旧摘要基础上生成渐进式摘要，避免信息丢失。
    """
    session_id: str = Field(..., validation_alias="sessionId", description="会话ID")
    memoryMessages: List[MemoryMessage] = Field(..., min_length=1, description="待整理的对话列表")
    memoryPreferenceVOList: List[MemoryPreferenceVO] = Field(default_factory=list, description="已有的偏好列表（用于冲突合并）")
    memoryUnresolvedVOList: List[MemoryUnresolvedVO] = Field(default_factory=list, description="已有的未完成事项列表（用于判断是否解决）")
    previousSummary: Optional[str] = Field(default=None, description="上一轮整合产出的摘要，用于生成渐进式摘要")


# ==================== 检修案例沉淀相关 ====================

class CaseDraftRequest(BaseModel):
    """
    检修案例草稿生成请求

    【功能关联】POST /ai/case/draft
    【何时用】Java 端把检修任务/文件/语音等原始材料交给 Python，
    由 AI 整理成结构化检修案例草稿（含一轮 Basic Reflection 自检）。

    【字段说明】
    - source_type: 材料来源（task/file/note_photo/voice）
    - task_context: 任务拼装文本
    - raw_text: 文件/OCR/语音转写文本
    - images: 相关图片 URL 列表
    """
    source_type: str = "task"            # task/file/note_photo/voice
    task_context: Optional[str] = None   # 任务拼装文本
    raw_text: Optional[str] = None       # 文件/OCR/语音转写文本
    images: Optional[List[str]] = None


class CaseComplianceRequest(BaseModel):
    """
    案例内容合规审核请求

    【功能关联】POST /ai/case/compliance
    【何时用】内容入库前由门控 LLM 判断是否相关、合法。

    【字段说明】
    - text: 待审核文本
    """
    text: str


class CaseExtractFile(BaseModel):
    """上传文件（pdf/txt/docx）的 Base64 载体。"""
    name: str = ""                       # 原始文件名（带扩展名，用于判定类型）
    content_base64: str = ""             # 文件字节的 Base64


class CaseExtractRequest(BaseModel):
    """
    案例素材抽取请求（文件/图片 → 纯文本）

    【功能关联】POST /ai/case/extract
    【何时用】工人通过"上传经验"通道提交文件(pdf/txt/docx)或笔记照片时，
    由 Python 抽取文档文字 + VLM 识别图片文字，汇成纯文本，再交给 /ai/case/draft 起草。

    【字段说明】
    - files: 待抽取的文档列表（Base64）
    - images: 笔记照片（URL 或 data-uri/base64），走 VLM OCR
    """
    files: Optional[List[CaseExtractFile]] = None
    images: Optional[List[str]] = None


class ValidateRequest(BaseModel):
    """
    通用入口校验请求（守门 LLM 泛化）

    【功能关联】POST /ai/validate
    【何时用】task：员工创建检修任务时挡乱码/无关垃圾（宽松相关性）；
             case：案例入库前的相关性+合规性判定（复用 check_compliance）。

    【字段说明】
    - text: 待校验文本
    - purpose: task / case
    """
    text: str
    purpose: str = "task"   # task / case

