# Java 服务本地启动

Java 服务统一读取项目根目录的 `.env`，不再通过 `application-dev.yml` 保存或加载本地密钥。

## 启动

先启动 MySQL、Redis、Neo4j、RabbitMQ、MinIO 和 Python 服务，然后在 `weixiu` 目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

脚本会完成以下工作：

1. 读取项目根目录的 `../.env`，只向当前 Java 进程导出环境变量，不修改 `.env` 和 `application.yml`。
2. 校验 Java 启动必需的变量，并检查 `API_TOKEN` 与 `INTERNAL_TOKEN` 不相同。
3. 将 `localhost:9000` 形式的 MinIO 地址规范化为 `http://localhost:9000`。
4. 使用真实的 `application.yml` 启动 Spring Boot，不激活 `dev` profile。

如果 `.env` 不在默认位置，可以显式指定：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1 -EnvFile "D:\config\weixiu.env"
```

## 验证中间件连接

需要从 Java 客户端实际验证全部中间件时执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1 -VerifyMiddleware
```

该模式会依次执行 MySQL `SELECT 1`、Redis `PING`、Neo4j `RETURN 1`、RabbitMQ 连接及队列检查、MinIO 默认桶检查。任意一项失败都会令 Java 启动失败，避免只检查端口而误判服务可用。
