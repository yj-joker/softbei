# FixAgent（FastAPI）—— 依赖很轻，无 torch/本地 embedding（LLM/向量走百炼 API）
FROM python:3.12-slim
WORKDIR /app

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 先装依赖（requirements 不变时命中缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷源码（.dockerignore 已排除 .venv / .env，env 由 compose 注入）
COPY . .

EXPOSE 8000
# 生产不开 --reload；worker 数按 CPU 调（ReAct 多为 IO 等待，单 worker 也够，先 1 个稳妥）
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
