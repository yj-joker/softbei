#!/usr/bin/env bash
set -u

APP_ROOT="/opt/fix/maintai"
FIX_ENV="/etc/maintai/fixagent.env"
JAVA_ENV="/etc/maintai/weixiu.env"
SECRETS_ENV="/etc/maintai/install-secrets.env"
PYTHON="$APP_ROOT/app/FixAgent/.venv/bin/python"
FAILURES=0

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }

if [[ "$EUID" -ne 0 || ! -r "$FIX_ENV" || ! -r "$JAVA_ENV" || ! -r "$SECRETS_ENV" ]]; then
    echo "请使用 sudo bash /opt/fix/maintai/verify.sh 执行。" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/service-token.sh"
set -a
snapshot_service_env "$FIX_ENV" fix
snapshot_service_env "$JAVA_ENV" java
snapshot_service_env "$SECRETS_ENV" secrets
set +a

check_nonempty() {
    local name="$1" value="$2"
    [[ -n "$value" ]] && pass "令牌非空：${name}" || fail "令牌非空：${name}"
}
check_equal() {
    local name="$1" left="$2" right="$3"
    [[ "$left" == "$right" ]] && pass "令牌方向一致：${name}" || fail "令牌方向不一致：${name}"
}

check_nonempty 'FixAgent API_TOKEN' "$FIX_API_TOKEN"
check_nonempty 'FixAgent INTERNAL_TOKEN' "$FIX_INTERNAL_TOKEN"
check_nonempty 'Java AI_API_TOKEN' "$JAVA_API_TOKEN"
check_nonempty 'Java INTERNAL_TOKEN' "$JAVA_INTERNAL_TOKEN"
check_nonempty 'install API_TOKEN' "$SECRET_API_TOKEN"
check_nonempty 'install INTERNAL_TOKEN' "$SECRET_INTERNAL_TOKEN"
check_equal 'FixAgent API_TOKEN = Java AI_API_TOKEN' "$FIX_API_TOKEN" "$JAVA_API_TOKEN"
check_equal 'FixAgent INTERNAL_TOKEN = Java INTERNAL_TOKEN' "$FIX_INTERNAL_TOKEN" "$JAVA_INTERNAL_TOKEN"
check_equal 'install API_TOKEN = FixAgent API_TOKEN' "$SECRET_API_TOKEN" "$FIX_API_TOKEN"
check_equal 'install INTERNAL_TOKEN = FixAgent INTERNAL_TOKEN' "$SECRET_INTERNAL_TOKEN" "$FIX_INTERNAL_TOKEN"
[[ "$FIX_API_TOKEN" != "$FIX_INTERNAL_TOKEN" ]] && pass '两枚服务令牌不同' || fail '两枚服务令牌不同'
[[ -z "${JAVA_PROFILE:-}" ]] && pass 'Java使用默认Spring配置' || fail 'Java意外激活了Spring Profile'

[[ "$(uname -m)" == 'loongarch64' ]] && pass 'CPU架构为loongarch64' || fail 'CPU架构不是loongarch64'
[[ "$('/usr/lib/jvm/java-21/bin/java' -version 2>&1 | head -n1)" == *'21.'* ]] && pass 'JDK 21' || fail 'JDK 21'
[[ "$($PYTHON --version 2>&1)" == 'Python 3.11.6' ]] && pass 'Python 3.11.6黄金环境' || fail 'Python黄金环境版本'

for service in mysql84 rabbitmq neo4j minio maintai-fixagent maintai-java nginx; do
    systemctl is-active --quiet "$service.service" && pass "systemd服务${service}" || fail "systemd服务${service}"
done

for port in 80 3306 5672 7474 7687 8000 8080 9000 9001 15672; do
    if ss -lnt | awk '{print $4}' | grep -qE "(^|:)${port}$"; then
        pass "TCP端口${port}监听"
    else
        fail "TCP端口${port}监听"
    fi
done

curl -fsS --max-time 5 http://127.0.0.1/healthz | grep -q 'MaintAI nginx OK' \
    && pass 'Nginx健康检查' || fail 'Nginx健康检查'
curl -fsS --max-time 5 http://127.0.0.1/ | grep -qi '<div id="app"' \
    && pass 'Vue生产页面' || fail 'Vue生产页面'
curl -fsS --max-time 5 http://127.0.0.1:8000/docs >/dev/null \
    && pass 'FixAgent接口' || fail 'FixAgent接口'

set -a
# shellcheck disable=SC1090
source "$FIX_ENV"
set +a

MYSQL_SCHEMA_RESULT="$(MYSQL_PWD="$MYSQL_PASSWORD" /opt/mysql-8.4/bin/mysql \
    -h127.0.0.1 -P3306 -u"$MYSQL_USER" -N -B -e "
SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='fix' AND table_name IN (
'user','ai_session','ai_message','memory_fact','maintenance_manual','knowledge_document','maintenance_task',
'task_step_record','maintenance_task_focus','task_graph_extraction_candidate','standard_procedure','procedure_step',
'manual_read_record','manual_device','memory_recall_trace','memory_reflection','task_chat_message','quiz_session',
'quiz_question','user_question_bank','knowledge_mastery','memory_dedup_state','memory_idempotent','expiration_review',
'maintenance_voice_event','operation_log','domain_rule','answer_feedback');
SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='fix' AND (
(table_name='maintenance_task' AND column_name IN ('evidence_bundle','extraction_status','resolution_status')) OR
(table_name='ai_message' AND column_name IN ('response_metadata','question_message_id')) OR
(table_name='expiration_review' AND column_name IN ('candidate_node_type','dedup_key')));
SELECT COUNT(*) FROM fix.user WHERE username IN ('3','4') AND status=1;" 2>/dev/null || true)"
[[ "$(echo "$MYSQL_SCHEMA_RESULT" | sed -n '1p')" == '28' ]] && pass 'MySQL 28张必要表' || fail 'MySQL必要表不完整'
[[ "$(echo "$MYSQL_SCHEMA_RESULT" | sed -n '2p')" == '7' ]] && pass 'MySQL关键升级字段' || fail 'MySQL关键升级字段不完整'
[[ "$(echo "$MYSQL_SCHEMA_RESULT" | sed -n '3p')" == '2' ]] && pass '演示账号3和4' || fail '演示账号3和4'

LOGIN_RESULT="$(mktemp)"
LOGIN_CODE="$(curl -sS -o "$LOGIN_RESULT" -w '%{http_code}' --max-time 15 \
    -H 'Content-Type: application/json' -d '{"username":"3","password":"123456"}' \
    http://127.0.0.1:8080/weixiu/user/login || true)"
if [[ "$LOGIN_CODE" == '200' ]] && grep -q '"code":"200"' "$LOGIN_RESULT"; then
    pass '管理员3登录接口'
else
    fail '管理员3登录接口'
fi
rm -f "$LOGIN_RESULT"

NEO4J_RESULT="$(env JAVA_HOME=/usr/lib/jvm/java-21 /opt/fix/neo4j/bin/cypher-shell \
    -a bolt://127.0.0.1:7687 -u "$NEO4J_USERNAME" -p "$NEO4J_PASSWORD" \
    --format plain "SHOW INDEXES YIELD type, state WHERE state = 'ONLINE' AND type <> 'LOOKUP' RETURN count(*) AS online_count" 2>/dev/null || true)"
echo "$NEO4J_RESULT" | grep -qE '(^|[^0-9])11([^0-9]|$)' \
    && pass 'Neo4j 11个项目索引ONLINE' || fail 'Neo4j项目索引'

curl -fsS --max-time 8 --user "$RABBITMQ_USER:$RABBITMQ_PASSWORD" \
    http://127.0.0.1:15672/api/overview | grep -q 'rabbitmq_version' \
    && pass 'RabbitMQ管理接口及专用账号' || fail 'RabbitMQ管理接口'

curl -fsS --max-time 8 http://127.0.0.1/files/weixiu-public-tupian/.maintai-health \
    | grep -q 'MaintAI MinIO' \
    && pass 'MinIO公开图片桶经Nginx访问' || fail 'MinIO桶或公开策略'

if "$PYTHON" - <<'PY'
import os
import sys
import redis

root = "/opt/fix/maintai/app/FixAgent"
os.chdir(root)
sys.path.insert(0, root)
from services.knowledge.vector_service import VectorService
VectorService()

common = {
    "host": os.environ["REDIS_HOST"],
    "port": int(os.getenv("REDIS_PORT", "6379")),
    "password": os.getenv("REDIS_PASSWORD") or None,
    "socket_timeout": 8,
}
assert redis.Redis(db=0, **common).ping()
assert redis.Redis(db=1, **common).ping()
indexes = redis.Redis(db=0, **common).execute_command("FT._LIST")
normalized = {x.decode() if isinstance(x, bytes) else str(x) for x in indexes}
assert "knowledge_vectors_v2" in normalized
PY
then
    pass '远程RediSearch DB0及Redis DB1'
else
    fail '远程RediSearch连接或索引'
fi

echo
if [[ "$FAILURES" -eq 0 ]]; then
    echo 'MaintAI automated deployment verification: ALL PASS'
    exit 0
fi

echo "MaintAI automated deployment verification: ${FAILURES} failure(s)" >&2
exit 10
