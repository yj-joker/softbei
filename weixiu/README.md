# Java service startup

The Java application loads `.env` before Spring resolves placeholders in
`application.yml`. This works on Windows, x86 Linux and LoongArch Linux without
requiring PowerShell on the server.

The loader searches these locations in order:

1. `DOTENV_FILE` or JVM option `-Ddotenv.file=...` when explicitly configured.
2. `.env` in the current working directory.
3. `.env` in the parent of the current working directory.
4. `.env` beside the executable Jar.

Operating-system environment variables, JVM `-D` properties and Spring command
line arguments have higher precedence than values from `.env`.

## Prepare configuration

From the repository root:

```powershell
Copy-Item .env.example .env
```

Fill every required secret and keep `API_TOKEN` different from
`INTERNAL_TOKEN`. The real `.env` is ignored by Git and must not be committed.

## Windows

Direct startup from IDEA now loads the repository-root `.env` automatically.
Maven startup is also supported:

```powershell
cd weixiu
mvn -Dmaven.test.skip=true spring-boot:run
```

The PowerShell wrapper remains available for strict variable validation and
middleware connectivity checks:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1 -VerifyMiddleware
```

## Linux and LoongArch

Install a LoongArch-compatible JDK 21, then run the same architecture-neutral
Spring Boot Jar:

```bash
java -jar weixiu.jar
```

When `.env` is stored elsewhere:

```bash
DOTENV_FILE=/etc/softbei/softbei.env java -jar weixiu.jar
```

or:

```bash
java -Ddotenv.file=/etc/softbei/softbei.env -jar weixiu.jar
```
