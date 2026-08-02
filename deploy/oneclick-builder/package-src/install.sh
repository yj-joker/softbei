#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

if [[ "$EUID" -ne 0 ]]; then
    echo "请使用 sudo bash install.sh 执行。" >&2
    exit 1
fi

PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd /
APP_ROOT="/opt/fix/maintai"
CONFIG_ROOT="/etc/maintai"
STATE_ROOT="/var/lib/maintai-installer"
LOG_ROOT="/var/log/maintai-installer"
INSTALL_CONFIG="$PACKAGE_ROOT/config/install.env"
SECRETS_FILE="$CONFIG_ROOT/install-secrets.env"
RUNTIME_ARCHIVE="$PACKAGE_ROOT/assets/MaintAI-LoongArch-Runtime.tar.gz"
APP_USER="maintai"
APP_GROUP="maintai"
source "$PACKAGE_ROOT/lib/service-token.sh"

mkdir -p "$LOG_ROOT"
LOG_FILE="$LOG_ROOT/install-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

CURRENT_STAGE="启动"
trap 'rc=$?; echo "[FAIL] 阶段：${CURRENT_STAGE}，退出码：${rc}" >&2; echo "日志：${LOG_FILE}" >&2; exit "$rc"' ERR

log() {
    printf '\n[MaintAI] %s\n' "$*"
}

die() {
    echo "[MaintAI][错误] $*" >&2
    exit 1
}

fresh_cleanup() {
    [[ "$PACKAGE_ROOT" != /opt/fix/* ]] || die "--fresh模式下请把安装包放在/home或/tmp等目录，不能放在/opt/fix内"

    CURRENT_STAGE="清理旧MaintAI部署"
    log "$CURRENT_STAGE"
    echo "将删除旧MaintAI应用、本机MySQL、RabbitMQ、Neo4j、MinIO、JDK 21及其项目数据。"

    local service
    for service in maintai-java maintai-fixagent minio neo4j rabbitmq mysql84 nginx; do
        systemctl disable --now "${service}.service" >/dev/null 2>&1 || true
    done

    pkill -u maintai >/dev/null 2>&1 || true
    pkill -u mysql84 >/dev/null 2>&1 || true

    rm -f \
        /etc/systemd/system/maintai-java.service \
        /etc/systemd/system/maintai-fixagent.service \
        /etc/systemd/system/minio.service \
        /etc/systemd/system/neo4j.service \
        /etc/systemd/system/rabbitmq.service \
        /etc/systemd/system/mysql84.service

    rm -rf -- \
        /opt/mysql-8.4 \
        /opt/fix \
        /usr/lib/jvm/java-21 \
        /etc/maintai \
        /etc/mysql-oracle \
        /etc/nginx/maintai.d \
        /var/lib/mysql-oracle \
        /var/log/mysql-oracle \
        /run/mysql-oracle \
        /var/lib/maintai-rabbitmq \
        /var/log/maintai-rabbitmq \
        /var/lib/maintai-neo4j \
        /var/log/maintai-neo4j \
        /run/maintai-neo4j \
        /var/lib/maintai-minio \
        /var/lib/maintai-files \
        /var/lib/maintai-installer

    systemctl daemon-reload
    systemctl reset-failed >/dev/null 2>&1 || true
    echo "[PASS] 旧MaintAI部署已清理"
}

wait_for_port() {
    local name="$1" port="$2" attempts="${3:-90}"
    local i
    for ((i=1; i<=attempts; i++)); do
        if timeout 2 bash -c "</dev/tcp/127.0.0.1/${port}" >/dev/null 2>&1; then
            echo "[PASS] ${name}端口${port}已就绪"
            return 0
        fi
        sleep 2
    done
    systemctl --no-pager --full status "$name" 2>/dev/null || true
    return 1
}

wait_for_url() {
    local name="$1" url="$2" attempts="${3:-90}"
    local i
    for ((i=1; i<=attempts; i++)); do
        if curl -fsS --max-time 4 "$url" >/dev/null 2>&1; then
            echo "[PASS] ${name}已就绪：${url}"
            return 0
        fi
        sleep 2
    done
    return 1
}

random_secret() {
    openssl rand -hex 18
}

validate_env_value() {
    local name="$1" value="$2"
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        die "${name}不能包含换行符"
    fi
}

validate_safe_token() {
    local name="$1" value="$2"
    if [[ ! "$value" =~ ^[A-Za-z0-9._:@%+=,-]*$ ]]; then
        die "${name}只能包含字母、数字及 ._:@%+=,-；建议留空让安装器自动生成"
    fi
}

ensure_service_user() {
    if ! getent group "$APP_GROUP" >/dev/null; then
        groupadd --system "$APP_GROUP"
    fi
    if ! id "$APP_USER" >/dev/null 2>&1; then
        useradd --system --gid "$APP_GROUP" --home-dir "$APP_ROOT" --shell /sbin/nologin "$APP_USER"
    fi
}

install_runtime_dir() {
    local source="$1" target="$2" marker="$3"
    if [[ -e "$target/$marker" ]]; then
        echo "[SKIP] 已存在运行时：${target}"
        return 0
    fi
    [[ -e "$source/$marker" ]] || die "运行时缺少 ${source}/${marker}"
    mkdir -p "$(dirname "$target")"
    cp -a "$source" "$target"
}

for argument in "$@"; do
    case "$argument" in
        --fresh|-f)
            fresh_cleanup
            ;;
        *)
            die "未知参数：${argument}。全新清理部署请使用：sudo bash install.sh --fresh"
            ;;
    esac
done

CURRENT_STAGE="架构与安装包检查"
log "$CURRENT_STAGE"
if command -v sha256sum >/dev/null 2>&1 && [[ -f "$PACKAGE_ROOT/SHA256SUMS.txt" ]]; then
    (cd "$PACKAGE_ROOT" && sha256sum -c SHA256SUMS.txt)
fi
[[ "$(uname -m)" == "loongarch64" ]] || die "只支持loongarch64，当前为$(uname -m)"
[[ -r /etc/os-release ]] || die "无法识别操作系统"
source /etc/os-release
[[ "${ID:-}" == "kylin" && "${VERSION_ID:-}" == "V11" ]] || die "只支持银河麒麟V11，当前为${PRETTY_NAME:-未知}"
[[ -f "$RUNTIME_ARCHIVE" ]] || die "缺少黄金运行时：$RUNTIME_ARCHIVE"
[[ -f "$PACKAGE_ROOT/app/weixiu.jar" ]] || die "缺少Java应用JAR"
[[ -f "$PACKAGE_ROOT/app/FixAgent/api/main.py" ]] || die "缺少FixAgent源码"
[[ -f "$PACKAGE_ROOT/frontend/index.html" ]] || die "缺少Vue构建产物"
[[ -f "$PACKAGE_ROOT/sql/fix.sql" ]] || die "缺少MySQL初始化脚本"
[[ -f "$PACKAGE_ROOT/neo4j/neo4j-indexes.cypher" ]] || die "缺少Neo4j索引脚本"

FREE_KB="$(df -Pk /opt | awk 'NR==2 {print $4}')"
[[ "$FREE_KB" -ge 8388608 ]] || die "/opt可用空间不足8GiB"

CURRENT_STAGE="安装银河麒麟基础依赖"
log "$CURRENT_STAGE"
command -v dnf >/dev/null || die "系统缺少dnf"
dnf install -y \
    python3 python3-pip nginx erlang \
    openssl curl tar gzip xz unzip \
    libaio libtirpc ncurses-libs libevent zlib

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$PYTHON_VERSION" == "3.11" ]] || die "黄金虚拟环境要求Python 3.11，当前为${PYTHON_VERSION}"

CURRENT_STAGE="生成部署参数与服务密码"
log "$CURRENT_STAGE"
ensure_service_user
install -d -o root -g "$APP_GROUP" -m 0750 "$CONFIG_ROOT"
install -d -o root -g root -m 0750 "$STATE_ROOT"
if [[ -f "$INSTALL_CONFIG" || -f "$SECRETS_FILE" ]]; then
    load_service_token_files "$INSTALL_CONFIG" "$SECRETS_FILE"
fi

if [[ -d /var/lib/mysql-oracle/mysql && ! -f "$SECRETS_FILE" && -z "${MYSQL_ROOT_PASSWORD:-}" ]]; then
    die "检测到旧MySQL数据但缺少原部署密钥。请清理旧数据后执行首次安装，或在config/install.env中提供原MYSQL_ROOT_PASSWORD"
fi

REDIS_HOST="${REDIS_HOST:-47.115.202.215}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(random_secret)}"
MYSQL_APP_USER="${MYSQL_APP_USER:-weixiu}"
MYSQL_APP_PASSWORD="${MYSQL_APP_PASSWORD:-$(random_secret)}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-$(random_secret)}"
RABBITMQ_USER="${RABBITMQ_USER:-maintai}"
RABBITMQ_PASSWORD="${RABBITMQ_PASSWORD:-$(random_secret)}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-maintaiadmin}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-$(random_secret)}"
API_TOKEN="${API_TOKEN:-}"
INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
resolve_service_tokens

if [[ -z "$DASHSCOPE_API_KEY" && -t 0 ]]; then
    read -r -s -p "请输入阿里云百炼DASHSCOPE_API_KEY：" DASHSCOPE_API_KEY
    echo
fi
[[ -n "$DASHSCOPE_API_KEY" ]] || die "必须在config/install.env中填写DASHSCOPE_API_KEY，或在安装时输入"

for item in REDIS_PASSWORD DASHSCOPE_API_KEY MYSQL_ROOT_PASSWORD MYSQL_APP_PASSWORD NEO4J_PASSWORD RABBITMQ_PASSWORD MINIO_SECRET_KEY API_TOKEN INTERNAL_TOKEN; do
    validate_env_value "$item" "${!item}"
    validate_safe_token "$item" "${!item}"
done

cat > "$SECRETS_FILE" <<EOF
REDIS_HOST=${REDIS_HOST}
REDIS_PORT=${REDIS_PORT}
REDIS_PASSWORD=${REDIS_PASSWORD}
DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}
MYSQL_APP_USER=${MYSQL_APP_USER}
MYSQL_APP_PASSWORD=${MYSQL_APP_PASSWORD}
NEO4J_PASSWORD=${NEO4J_PASSWORD}
RABBITMQ_USER=${RABBITMQ_USER}
RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}
MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}
MINIO_SECRET_KEY=${MINIO_SECRET_KEY}
EOF
write_service_token_secrets "$SECRETS_FILE"

CURRENT_STAGE="安装LoongArch黄金运行时"
log "$CURRENT_STAGE"
install -d -o root -g root -m 0755 /opt/fix
RUNTIME_STAGE="$(mktemp -d /var/tmp/maintai-runtime.XXXXXX)"
trap 'rm -rf -- "$RUNTIME_STAGE"' EXIT
tar -xzf "$RUNTIME_ARCHIVE" -C "$RUNTIME_STAGE" runtime
install_runtime_dir "$RUNTIME_STAGE/runtime/mysql-8.4" /opt/mysql-8.4 bin/mysqld
install_runtime_dir "$RUNTIME_STAGE/runtime/neo4j" /opt/fix/neo4j bin/neo4j
install_runtime_dir "$RUNTIME_STAGE/runtime/rabbitmq" /opt/fix/rabbitmq sbin/rabbitmq-server
install_runtime_dir "$RUNTIME_STAGE/runtime/java-21" /usr/lib/jvm/java-21 bin/java
ln -sfn ../cypher-shell/bin/cypher-shell /opt/fix/neo4j/bin/cypher-shell
chown -h "$APP_USER:$APP_GROUP" /opt/fix/neo4j/bin/cypher-shell
mkdir -p /opt/fix/minio
if [[ ! -x /opt/fix/minio/minio ]]; then
    install -m 0755 "$RUNTIME_STAGE/runtime/minio/minio" /opt/fix/minio/minio
fi

[[ "$(/usr/lib/jvm/java-21/bin/java -version 2>&1 | head -n1)" == *'21.'* ]] || die "JDK 21运行时验证失败"
[[ "$(/opt/mysql-8.4/bin/mysqld --version)" == *'8.4.10'*loongarch64* ]] || die "MySQL LoongArch运行时验证失败"
[[ "$(/opt/fix/minio/minio --version | head -n1)" == *'RELEASE.2025-04-22'* ]] || die "MinIO运行时验证失败"

CURRENT_STAGE="初始化MySQL 8.4.10"
log "$CURRENT_STAGE"
getent group mysql84 >/dev/null || groupadd --system mysql84
id mysql84 >/dev/null 2>&1 || useradd --system --gid mysql84 --home-dir /var/lib/mysql-oracle --shell /sbin/nologin mysql84
mkdir -p /var/lib/mysql-oracle /var/log/mysql-oracle /var/run/mysql-oracle /etc/mysql-oracle
chown -R mysql84:mysql84 /var/lib/mysql-oracle /var/log/mysql-oracle /var/run/mysql-oracle
cat > /etc/mysql-oracle/my.cnf <<'EOF'
[client]
port=3306
socket=/var/run/mysql-oracle/mysql.sock
default-character-set=utf8mb4

[mysqld]
user=mysql84
basedir=/opt/mysql-8.4
datadir=/var/lib/mysql-oracle
port=3306
bind-address=127.0.0.1
socket=/var/run/mysql-oracle/mysql.sock
pid-file=/var/run/mysql-oracle/mysqld.pid
log-error=/var/log/mysql-oracle/error.log
character-set-server=utf8mb4
collation-server=utf8mb4_0900_ai_ci
skip-name-resolve
mysqlx=0
EOF
chmod 0755 /etc/mysql-oracle
chmod 0644 /etc/mysql-oracle/my.cnf

cat > /etc/systemd/system/mysql84.service <<'EOF'
[Unit]
Description=MaintAI MySQL Community Server 8.4.10
After=network.target

[Service]
Type=simple
User=mysql84
Group=mysql84
RuntimeDirectory=mysql-oracle
RuntimeDirectoryMode=0755
ExecStart=/opt/mysql-8.4/bin/mysqld --defaults-file=/etc/mysql-oracle/my.cnf
Restart=on-failure
RestartSec=5
TimeoutStartSec=300
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

MYSQL_NEW=0
if [[ ! -d /var/lib/mysql-oracle/mysql ]]; then
    MYSQL_NEW=1
    /opt/mysql-8.4/bin/mysqld --defaults-file=/etc/mysql-oracle/my.cnf --initialize-insecure --user=mysql84
fi
systemctl daemon-reload
systemctl enable mysql84.service >/dev/null
systemctl restart mysql84.service
wait_for_port mysql84.service 3306 120
MYSQL_SOCKET=/var/run/mysql-oracle/mysql.sock
if [[ "$MYSQL_NEW" -eq 1 ]]; then
    /opt/mysql-8.4/bin/mysql --socket="$MYSQL_SOCKET" -uroot --skip-password <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';
SQL
fi
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" /opt/mysql-8.4/bin/mysql --socket="$MYSQL_SOCKET" -uroot <<SQL
CREATE DATABASE IF NOT EXISTS fix CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS '${MYSQL_APP_USER}'@'127.0.0.1' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
CREATE USER IF NOT EXISTS '${MYSQL_APP_USER}'@'localhost' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
ALTER USER '${MYSQL_APP_USER}'@'127.0.0.1' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
ALTER USER '${MYSQL_APP_USER}'@'localhost' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
GRANT ALL PRIVILEGES ON fix.* TO '${MYSQL_APP_USER}'@'127.0.0.1';
GRANT ALL PRIVILEGES ON fix.* TO '${MYSQL_APP_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL
EXISTING_TABLES="$(MYSQL_PWD="$MYSQL_ROOT_PASSWORD" /opt/mysql-8.4/bin/mysql --socket="$MYSQL_SOCKET" -uroot -N -B \
    -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='fix';")"
if [[ "$EXISTING_TABLES" == "0" ]]; then
    MYSQL_PWD="$MYSQL_ROOT_PASSWORD" /opt/mysql-8.4/bin/mysql --socket="$MYSQL_SOCKET" -uroot fix < "$PACKAGE_ROOT/sql/fix.sql"
elif [[ "$EXISTING_TABLES" == "25" ]]; then
    echo "[SKIP] fix数据库已经包含25张表，不重复执行完整建表脚本"
else
    die "fix数据库处于半初始化状态：当前${EXISTING_TABLES}张表，预期0或25张。请先备份并清理后重试"
fi
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" /opt/mysql-8.4/bin/mysql --socket="$MYSQL_SOCKET" -uroot fix <<'SQL'
INSERT INTO `user`
    (`username`, `name`, `number`, `password`, `gender`, `type`, `phone`, `email`, `hire_date`, `status`)
VALUES
    ('3', '管理员3', 'ADMIN0003', '$2a$04$oFyOSL53vtwxJFddR99GeOw2ze/mOZ6ftQwqFm8yBfUGqH/Rm0VXu', 0, 1, '13800000003', NULL, CURRENT_DATE, 1),
    ('4', '普通用户4', 'USER0004', '$2a$04$oFyOSL53vtwxJFddR99GeOw2ze/mOZ6ftQwqFm8yBfUGqH/Rm0VXu', 0, 0, '13800000004', NULL, CURRENT_DATE, 1)
ON DUPLICATE KEY UPDATE
    `name` = VALUES(`name`), `password` = VALUES(`password`), `gender` = VALUES(`gender`),
    `type` = VALUES(`type`), `phone` = VALUES(`phone`), `email` = VALUES(`email`),
    `hire_date` = VALUES(`hire_date`), `status` = VALUES(`status`), `update_time` = CURRENT_TIMESTAMP;
SQL

CURRENT_STAGE="初始化RabbitMQ"
log "$CURRENT_STAGE"
mkdir -p /var/lib/maintai-rabbitmq/mnesia /var/log/maintai-rabbitmq
chown -R "$APP_USER:$APP_GROUP" /opt/fix/rabbitmq /var/lib/maintai-rabbitmq /var/log/maintai-rabbitmq
cat > "$CONFIG_ROOT/rabbitmq.conf" <<'EOF'
listeners.tcp.default = 127.0.0.1:5672
management.tcp.ip = 127.0.0.1
management.tcp.port = 15672
EOF
chmod 0640 "$CONFIG_ROOT/rabbitmq.conf"
chown root:"$APP_GROUP" "$CONFIG_ROOT/rabbitmq.conf"
cat > /etc/systemd/system/rabbitmq.service <<EOF
[Unit]
Description=MaintAI RabbitMQ 3.12.14
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=/opt/fix/rabbitmq
Environment=HOME=/var/lib/maintai-rabbitmq
Environment=RABBITMQ_HOME=/opt/fix/rabbitmq
Environment=RABBITMQ_MNESIA_BASE=/var/lib/maintai-rabbitmq/mnesia
Environment=RABBITMQ_LOG_BASE=/var/log/maintai-rabbitmq
Environment=RABBITMQ_ENABLED_PLUGINS_FILE=/var/lib/maintai-rabbitmq/enabled_plugins
Environment=RABBITMQ_CONFIG_FILE=${CONFIG_ROOT}/rabbitmq
ExecStart=/opt/fix/rabbitmq/sbin/rabbitmq-server
ExecStop=/opt/fix/rabbitmq/sbin/rabbitmqctl shutdown
Restart=on-failure
RestartSec=8
TimeoutStartSec=240
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
runuser -u "$APP_USER" -- env HOME=/var/lib/maintai-rabbitmq RABBITMQ_HOME=/opt/fix/rabbitmq RABBITMQ_ENABLED_PLUGINS_FILE=/var/lib/maintai-rabbitmq/enabled_plugins /opt/fix/rabbitmq/sbin/rabbitmq-plugins enable --offline rabbitmq_management
systemctl daemon-reload
systemctl enable rabbitmq.service >/dev/null
systemctl restart rabbitmq.service
wait_for_port rabbitmq.service 5672 120
RABBIT_CTL=(runuser -u "$APP_USER" -- env HOME=/var/lib/maintai-rabbitmq RABBITMQ_HOME=/opt/fix/rabbitmq RABBITMQ_MNESIA_BASE=/var/lib/maintai-rabbitmq/mnesia /opt/fix/rabbitmq/sbin/rabbitmqctl)
if ! "${RABBIT_CTL[@]}" list_users | awk 'NR > 1 {print $1}' | grep -Fxq "$RABBITMQ_USER"; then
    "${RABBIT_CTL[@]}" add_user "$RABBITMQ_USER" "$RABBITMQ_PASSWORD"
else
    "${RABBIT_CTL[@]}" change_password "$RABBITMQ_USER" "$RABBITMQ_PASSWORD"
fi
"${RABBIT_CTL[@]}" set_permissions -p / "$RABBITMQ_USER" '.*' '.*' '.*'
"${RABBIT_CTL[@]}" set_user_tags "$RABBITMQ_USER" management

CURRENT_STAGE="初始化Neo4j与项目索引"
log "$CURRENT_STAGE"
mkdir -p /var/lib/maintai-neo4j/data /var/log/maintai-neo4j /run/maintai-neo4j
chown -R "$APP_USER:$APP_GROUP" /opt/fix/neo4j /var/lib/maintai-neo4j /var/log/maintai-neo4j /run/maintai-neo4j
cat > /opt/fix/neo4j/conf/neo4j.conf <<'EOF'
server.default_listen_address=127.0.0.1
server.bolt.enabled=true
server.bolt.listen_address=:7687
server.http.enabled=true
server.http.listen_address=:7474
server.https.enabled=false
server.directories.data=/var/lib/maintai-neo4j/data
server.directories.logs=/var/log/maintai-neo4j
server.directories.run=/run/maintai-neo4j
dbms.security.auth_enabled=true
server.memory.heap.initial_size=512m
server.memory.heap.max_size=2g
server.memory.pagecache.size=1g
EOF
cat > /etc/systemd/system/neo4j.service <<EOF
[Unit]
Description=MaintAI Neo4j 5.26
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=/opt/fix/neo4j
Environment=HOME=/var/lib/maintai-neo4j
Environment=JAVA_HOME=/usr/lib/jvm/java-21
Environment=NEO4J_CONF=/opt/fix/neo4j/conf
ExecStart=/opt/fix/neo4j/bin/neo4j console
Restart=on-failure
RestartSec=10
TimeoutStartSec=240
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
if [[ ! -f /var/lib/maintai-neo4j/data/dbms/auth ]]; then
    runuser -u "$APP_USER" -- env HOME="$APP_ROOT" JAVA_HOME=/usr/lib/jvm/java-21 NEO4J_CONF=/opt/fix/neo4j/conf /opt/fix/neo4j/bin/neo4j-admin dbms set-initial-password "$NEO4J_PASSWORD" --require-password-change=false
fi
systemctl daemon-reload
systemctl enable neo4j.service >/dev/null
systemctl restart neo4j.service
wait_for_port neo4j.service 7687 150
runuser -u "$APP_USER" -- env JAVA_HOME=/usr/lib/jvm/java-21 /opt/fix/neo4j/bin/cypher-shell \
    -a bolt://127.0.0.1:7687 -u neo4j -p "$NEO4J_PASSWORD" \
    < "$PACKAGE_ROOT/neo4j/neo4j-indexes.cypher"

CURRENT_STAGE="初始化MinIO"
log "$CURRENT_STAGE"
mkdir -p /var/lib/maintai-minio/data
chown -R "$APP_USER:$APP_GROUP" /opt/fix/minio /var/lib/maintai-minio
cat > "$CONFIG_ROOT/minio.env" <<EOF
MINIO_ROOT_USER=${MINIO_ACCESS_KEY}
MINIO_ROOT_PASSWORD=${MINIO_SECRET_KEY}
EOF
chmod 0640 "$CONFIG_ROOT/minio.env"
chown root:"$APP_GROUP" "$CONFIG_ROOT/minio.env"
cat > /etc/systemd/system/minio.service <<EOF
[Unit]
Description=MaintAI MinIO Object Storage
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=/opt/fix/minio
EnvironmentFile=${CONFIG_ROOT}/minio.env
ExecStart=/opt/fix/minio/minio server /var/lib/maintai-minio/data --address 127.0.0.1:9000 --console-address 127.0.0.1:9001
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable minio.service >/dev/null
systemctl restart minio.service
wait_for_port minio.service 9000 90

CURRENT_STAGE="部署MaintAI应用"
log "$CURRENT_STAGE"
systemctl stop maintai-java.service maintai-fixagent.service 2>/dev/null || true
APP_STAGE="$(mktemp -d /opt/fix/maintai-app.XXXXXX)"
mkdir -p "$APP_STAGE/app" "$APP_STAGE/frontend" "$APP_STAGE/resources/sql" "$APP_STAGE/resources/neo4j" "$APP_STAGE/scripts"
mkdir -p "$APP_STAGE/lib"
cp -a "$PACKAGE_ROOT/app/." "$APP_STAGE/app/"
cp -a "$PACKAGE_ROOT/frontend/." "$APP_STAGE/frontend/"
cp -a "$PACKAGE_ROOT/sql/fix.sql" "$APP_STAGE/resources/sql/fix.sql"
cp -a "$PACKAGE_ROOT/neo4j/neo4j-indexes.cypher" "$APP_STAGE/resources/neo4j/neo4j-indexes.cypher"
cp -a "$PACKAGE_ROOT/scripts/configure-minio.py" "$APP_STAGE/scripts/configure-minio.py"
cp -a "$PACKAGE_ROOT/verify.sh" "$APP_STAGE/verify.sh"
copy_runtime_service_helper "$PACKAGE_ROOT/lib/service-token.sh" "$APP_STAGE/lib/service-token.sh"
rm -rf -- "$APP_STAGE/app/FixAgent/.venv"
cp -a "$RUNTIME_STAGE/runtime/fixagent-venv" "$APP_STAGE/app/FixAgent/.venv"
chmod 0755 "$APP_STAGE/app/FixAgent/.venv/bin/python" "$APP_STAGE/verify.sh"
chmod 0644 "$APP_STAGE/lib/service-token.sh"
if [[ -d "$APP_ROOT" ]]; then
    mv "$APP_ROOT" "${APP_ROOT}.previous.$(date +%Y%m%d-%H%M%S)"
fi
mv "$APP_STAGE" "$APP_ROOT"
chown -R "$APP_USER:$APP_GROUP" "$APP_ROOT"
chmod 0755 "$APP_ROOT"
chmod -R a+rX "$APP_ROOT/frontend"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 /var/lib/maintai-files

cat > "$CONFIG_ROOT/fixagent.env" <<EOF
DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
LLM_MODEL=qwen-plus
LLM_TEMPERATURE=0.7
LLM_TOP_P=0.9
REDIS_HOST=${REDIS_HOST}
REDIS_PORT=${REDIS_PORT}
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_DB=0
REDIS_TTL=86400
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=${NEO4J_PASSWORD}
NEO4J_DATABASE=neo4j
FILE_STORAGE_BACKEND=minio
FILE_PUBLIC_BASE_URL=/files
LOCAL_FILE_STORAGE_DIR=/var/lib/maintai-files
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}
MINIO_SECRET_KEY=${MINIO_SECRET_KEY}
MINIO_DOCUMENT_BUCKET=weixiu-private-wendang
MINIO_PUBLIC_IMAGE_BUCKET=weixiu-public-tupian
MINIO_PUBLIC_BASE_URL=/files/weixiu-public-tupian
MINIO_SECURE=false
IMAGE_SUMMARY_LLM_ENABLED=true
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=fix
MYSQL_USER=${MYSQL_APP_USER}
MYSQL_PASSWORD=${MYSQL_APP_PASSWORD}
JAVA_SERVICE_URL=http://127.0.0.1:8080
RABBITMQ_URL=amqp://${RABBITMQ_USER}:${RABBITMQ_PASSWORD}@127.0.0.1:5672/
EOF
chmod 0640 "$CONFIG_ROOT/fixagent.env"

cat > "$CONFIG_ROOT/weixiu.env" <<EOF
SPRING_PROFILES_ACTIVE=prod
DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
SPRING_DATASOURCE_URL=jdbc:mysql://127.0.0.1:3306/fix?useUnicode=true&characterEncoding=utf-8&useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true
SPRING_DATASOURCE_USERNAME=${MYSQL_APP_USER}
SPRING_DATASOURCE_PASSWORD=${MYSQL_APP_PASSWORD}
MYSQL_USER=${MYSQL_APP_USER}
MYSQL_PASSWORD=${MYSQL_APP_PASSWORD}
SPRING_NEO4J_URI=bolt://127.0.0.1:7687
SPRING_NEO4J_AUTHENTICATION_USERNAME=neo4j
SPRING_NEO4J_AUTHENTICATION_PASSWORD=${NEO4J_PASSWORD}
BOLT_URI=bolt://127.0.0.1:7687
NEO4J_PASSWORD=${NEO4J_PASSWORD}
SPRING_RABBITMQ_HOST=127.0.0.1
SPRING_RABBITMQ_PORT=5672
SPRING_RABBITMQ_USERNAME=${RABBITMQ_USER}
SPRING_RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}
RABBITMQ_HOST=127.0.0.1
RABBITMQ_PORT=5672
RABBITMQ_USER=${RABBITMQ_USER}
RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}
SPRING_DATA_REDIS_HOST=${REDIS_HOST}
SPRING_DATA_REDIS_PORT=${REDIS_PORT}
SPRING_DATA_REDIS_DATABASE=1
SPRING_DATA_REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_HOST=${REDIS_HOST}
REDIS_PORT=${REDIS_PORT}
REDIS_PASSWORD=${REDIS_PASSWORD}
PYTHON_SERVICE_URL=http://127.0.0.1:8000
MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_PUBLIC_BASE_URL=/files
MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}
MINIO_SECRET_KEY=${MINIO_SECRET_KEY}
MINIO_BUCKET=weixiu-private-wendang
EOF
render_service_token_envs "$CONFIG_ROOT/fixagent.env" "$CONFIG_ROOT/weixiu.env"
chmod 0640 "$CONFIG_ROOT/fixagent.env"
chmod 0640 "$CONFIG_ROOT/weixiu.env"
chown root:"$APP_GROUP" "$CONFIG_ROOT/fixagent.env" "$CONFIG_ROOT/weixiu.env"

cat > /etc/systemd/system/maintai-fixagent.service <<EOF
[Unit]
Description=MaintAI FixAgent AI Service
After=network-online.target mysql84.service rabbitmq.service neo4j.service minio.service
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_ROOT}/app/FixAgent
EnvironmentFile=${CONFIG_ROOT}/fixagent.env
ExecStart=${APP_ROOT}/app/FixAgent/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
TimeoutStartSec=180
UMask=0027
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/maintai-java.service <<EOF
[Unit]
Description=MaintAI Java Backend
After=network-online.target mysql84.service rabbitmq.service neo4j.service minio.service maintai-fixagent.service
Wants=network-online.target maintai-fixagent.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_ROOT}/app
EnvironmentFile=${CONFIG_ROOT}/weixiu.env
ExecStart=/usr/lib/jvm/java-21/bin/java -XX:InitialRAMPercentage=20 -XX:MaxRAMPercentage=70 -Dfile.encoding=UTF-8 -jar ${APP_ROOT}/app/weixiu.jar
Restart=on-failure
RestartSec=8
TimeoutStartSec=240
SuccessExitStatus=143
UMask=0027
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

CURRENT_STAGE="配置Nginx"
log "$CURRENT_STAGE"
mkdir -p /etc/nginx/maintai.d
install -m 0644 "$PACKAGE_ROOT/nginx/maintai.conf" /etc/nginx/maintai.d/maintai.conf
NGINX_USER="$(awk '/^[[:space:]]*user[[:space:]]+/ {gsub(/;/, "", $2); print $2; exit}' /etc/nginx/nginx.conf 2>/dev/null || true)"
NGINX_USER="${NGINX_USER:-nginx}"
cp -a /etc/nginx/nginx.conf "/etc/nginx/nginx.conf.before-maintai.$(date +%Y%m%d-%H%M%S)"
sed "s/__NGINX_USER__/${NGINX_USER}/g" "$PACKAGE_ROOT/nginx/nginx-main.conf.template" > /etc/nginx/nginx.conf
nginx -t

CURRENT_STAGE="创建MinIO桶与公开读取策略"
log "$CURRENT_STAGE"
runuser -u "$APP_USER" -- "$APP_ROOT/app/FixAgent/.venv/bin/python" "$APP_ROOT/scripts/configure-minio.py" "$CONFIG_ROOT/fixagent.env"

CURRENT_STAGE="启动MaintAI并自动验收"
log "$CURRENT_STAGE"
systemctl daemon-reload
systemctl enable nginx.service maintai-fixagent.service maintai-java.service >/dev/null
systemctl restart nginx.service
wait_for_url Nginx http://127.0.0.1/healthz 30
systemctl restart maintai-fixagent.service
if ! wait_for_url FixAgent http://127.0.0.1:8000/docs 120; then
    journalctl -u maintai-fixagent.service -n 150 --no-pager
    exit 1
fi
systemctl restart maintai-java.service
wait_for_port maintai-java.service 8080 150

bash "$APP_ROOT/verify.sh"
touch "$STATE_ROOT/install-complete"
chmod 0640 "$STATE_ROOT/install-complete"

echo
echo "MaintAI一键部署完成。"
echo "本机入口：http://127.0.0.1/"
echo "管理员账号：3 / 123456"
echo "普通用户：4 / 123456"
echo "自动生成的服务凭据：${SECRETS_FILE}（仅root可读）"
echo "安装日志：${LOG_FILE}"
