import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
    llm_model = os.getenv("LLM_MODEL", "qwen-plus")
    # LLM 推理 endpoint，默认阿里云百炼；换本地模型只需改 .env 中的 LLM_API_BASE
    # 示例（Ollama）: LLM_API_BASE=http://localhost:11434/v1
    llm_api_base = os.getenv("LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    intent_router_model = os.getenv("INTENT_ROUTER_MODEL", "qwen-turbo")
    vlm_model = os.getenv("VLM_MODEL", "qwen-vl-max")
    llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    llm_top_p = float(os.getenv("LLM_TOP_P", "0.9"))
    llm_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "4000"))

    # Redis 配置
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD", None)
    redis_db = int(os.getenv("REDIS_DB", "0"))
    redis_ttl = int(os.getenv("REDIS_TTL", "86400"))  # 缓存默认24小时过期

    # Java 后端服务地址（图谱查询统一走 Java 端，Python 不再直连 Neo4j）
    java_service_url = os.getenv("JAVA_SERVICE_URL", "http://localhost:8080")

    # RabbitMQ 配置
    rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

    # 内部服务鉴权令牌（与 Java 端 ai.internal-token 保持一致）
    internal_token = os.getenv("INTERNAL_TOKEN", "")

    # Python 端全站 API token（Java 调 Python 时携带，防止未授权访问）
    # 必须配置，未配置时 Python 服务将拒绝所有请求
    api_token = os.getenv("API_TOKEN", "")

    file_storage_backend = os.getenv("FILE_STORAGE_BACKEND", "local")
    file_public_base_url = os.getenv("FILE_PUBLIC_BASE_URL", "/files")
    local_file_storage_dir = os.getenv("LOCAL_FILE_STORAGE_DIR", "rag_files")
    minio_public_base_url = os.getenv("MINIO_PUBLIC_BASE_URL", "")
    minio_endpoint = os.getenv("MINIO_ENDPOINT", "")
    minio_access_key = os.getenv("MINIO_ACCESS_KEY", "")
    minio_secret_key = os.getenv("MINIO_SECRET_KEY", "")
    minio_bucket = os.getenv("MINIO_BUCKET", "fixagent-rag")
    minio_document_bucket = os.getenv("MINIO_DOCUMENT_BUCKET", minio_bucket)
    minio_public_image_bucket = os.getenv("MINIO_PUBLIC_IMAGE_BUCKET", minio_bucket)
    minio_secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
    image_summary_llm_enabled = os.getenv("IMAGE_SUMMARY_LLM_ENABLED", "false").lower() == "true"

    # MySQL metadata database used for structured knowledge inventory queries.
    mysql_host = os.getenv("MYSQL_HOST", "localhost")
    mysql_port = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_database = os.getenv("MYSQL_DATABASE", "fix")
    mysql_user = os.getenv("MYSQL_USER", "root")
    mysql_password = os.getenv("MYSQL_PASSWORD", "1234")


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
