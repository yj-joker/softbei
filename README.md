# 软件杯项目

本仓库整合了项目的三端代码：

| 目录 | 说明 | 技术栈 |
|------|------|--------|
| [`fix-`](./fix-) | 前端 | Vite / Vue |
| [`weixiu`](./weixiu) | Java 后端 | Spring Boot / Maven |
| [`FixAgent`](./FixAgent) | Python 智能体端 | Python / RAG |

## 克隆后启动

> 含真实密钥的配置文件（`weixiu/.../application-dev.yml`、`FixAgent/.env`）不在仓库中，
> 需从对应的 `.example` 模板复制后填入真实值。

**1. 准备配置文件**

```bash
# Java 端：active profile 为 dev，此文件必需
cp weixiu/src/main/resources/application-dev.yml.example weixiu/src/main/resources/application-dev.yml

# Python 端
cp FixAgent/.env.example FixAgent/.env
```

然后编辑这两个文件，填入：数据库密码、阿里云 OSS AccessKey、百炼 `DASHSCOPE_API_KEY` 等。

**2. 安装依赖并启动**

| 端 | 命令 |
|----|------|
| 前端 `fix-` | `npm install` → `npm run dev` |
| Java `weixiu` | `mvn package` → 运行 Spring Boot |
| Python `FixAgent` | 建虚拟环境 → `pip install -r requirements.txt` → 启动 API |

**3. 依赖中间件**：需本地或容器中运行 MySQL、Neo4j、Redis、RabbitMQ、MinIO（可用各目录下的 `Dockerfile` / 部署文件）。
"test commit from cwt" 
