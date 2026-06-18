# Config 模块

## 模块概述

Config 模块是 Python AI 模块的配置管理中心，负责管理 AI 推理相关的配置信息。

> **重要**：本模块管理 AI 推理相关配置（LLM、Embedding、Redis、Neo4j）和 Python RAG 导入链路需要的文件存储适配配置。MySQL 等业务数据配置仍由 Java 后端统一管理。

## 配置项详解

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|-------|---------|------|--------|------|
| DashScope API Key | `DASHSCOPE_API_KEY` | str | **必需** | 阿里云百炼API密钥 |
| Redis 主机 | `REDIS_HOST` | str | localhost | Redis服务器地址 |
| Redis 端口 | `REDIS_PORT` | int | 6379 | Redis服务端口 |
| LLM 模型 | `LLM_MODEL` | str | qwen-plus | 阿里云百炼模型名称 |
| Embedding模型 | `EMBEDDING_MODEL` | str | text-embedding-v4 | 向量化模型 |
| 文件存储后端 | `FILE_STORAGE_BACKEND` | str | local | `local` 或 `minio` |
| MinIO Endpoint | `MINIO_ENDPOINT` | str | 空 | 例如 `localhost:9000` |
| MinIO 兼容默认 Bucket | `MINIO_BUCKET` | str | fixagent-rag | 未单独配置双桶时的兼容默认值 |
| MinIO 文档 Bucket | `MINIO_DOCUMENT_BUCKET` | str | `MINIO_BUCKET` | 私有 PDF/文档对象 bucket |
| MinIO 公开图片 Bucket | `MINIO_PUBLIC_IMAGE_BUCKET` | str | `MINIO_BUCKET` | PDF 拆图和回显图片对象 bucket |
| MinIO 公开图片地址前缀 | `MINIO_PUBLIC_BASE_URL` | str | 空 | 公开图片对象 URL 前缀，图片向量化优先使用本地拆图路径 |

## 技术选型

| 组件 | 选型 | 理由 |
|-----|------|------|
| 配置管理 | python-dotenv | 加载.env文件，环境隔离 |
| 配置读取 | 标准 os.getenv | 简单直接，无需额外依赖 |

## 项目中的实现

### settings.py 实际实现

```python
# config/settings.py
"""
FixAgent 配置管理模块

所有配置通过环境变量或 .env 文件加载，确保敏感信息不硬编码。
"""

import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DASHSCOPE_API_KEY")
```

**说明**：当前实现采用极简设计，仅加载必要的 `DASHSCOPE_API_KEY`。后续随着 services 层实现，可扩展为完整的配置类。

### .env 文件示例

```bash
# .env 示例文件

# ==================== API Keys ====================
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# ==================== Redis（向量库） ====================
REDIS_HOST=localhost
REDIS_PORT=6379

# ==================== Neo4j（图谱） ====================
NEO4J_HOST=localhost
NEO4J_PORT=7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j123

# ==================== 模型配置 ====================
LLM_MODEL=qwen-plus
EMBEDDING_MODEL=text-embedding-v4

# ==================== RAG 文件存储 ====================
FILE_STORAGE_BACKEND=minio
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_DOCUMENT_BUCKET=weixiu-private-wendang
MINIO_PUBLIC_IMAGE_BUCKET=weixiu-public-tupian
MINIO_PUBLIC_BASE_URL=http://localhost:9000/weixiu-public-tupian
MINIO_SECURE=false
```

### 使用示例

```python
# 在 services/llm_service.py 中使用
from config.settings import API_KEY

client = OpenAI(
    api_key=API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
```

## 与Java后端的配置边界

> **架构边界**：Python AI 模块只负责 AI 推理，不碰业务数据。

| 配置类型 | 管理方 | 说明 |
|---------|-------|------|
| LLM API Key (DASHSCOPE_API_KEY) | **Python AI** | AI 推理必需 |
| Redis | **Python AI** | 向量库、缓存 |
| Neo4j | **Python AI** | 图谱查询 |
| MySQL | **Java 后端** | 业务数据（案例、设备等） |
| OSS / MinIO | **Java 后端为主，Python RAG 导入链路可适配** | Python 侧在 PDF 拆图入库时需要获得可访问 URL |
| ChatMemory | **Java 后端** | 会话管理 |

**对应关系**：

| Python配置 | Java配置 (application.yml) | 说明 |
|-----------|---------------------------|------|
| `DASHSCOPE_API_KEY` | `dashscope.api-key` | 百炼API密钥 |
| `REDIS_HOST` | `spring.data.redis.host` | Redis连接（由Java管理，Python通过Java调用） |

## 文件结构

```
config/
├── __init__.py
├── README.md                    # 本文件
└── settings.py                  # 配置加载（极简实现）
```

## 注意事项

1. **敏感信息保护**: `.env`文件不应提交到版本控制，需添加到`.gitignore`
2. **架构边界**: Python AI 模块不管理 MySQL、OSS 等业务数据配置
3. **配置扩展**: 随着 services 层实现，可扩展为 pydantic-settings 提供完整配置管理
