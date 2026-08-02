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

set -a
# shellcheck disable=SC1090
source "$FIX_ENV"
FIX_API_TOKEN="${API_TOKEN:-}"
FIX_INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
source "$JAVA_ENV"
JAVA_API_TOKEN="${AI_API_TOKEN:-}"
JAVA_INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
JAVA_PROFILE="${SPRING_PROFILES_ACTIVE:-}"
source "$SECRETS_ENV"
SECRET_API_TOKEN="${API_TOKEN:-}"
SECRET_INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
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
[[ "$JAVA_PROFILE" == 'prod' ]] && pass 'Java profile为prod' || fail 'Java profile不是prod'

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

echo
if [[ "$FAILURES" -eq 0 ]]; then
    echo 'MaintAI automated deployment verification: ALL PASS'
    exit 0
fi

echo "MaintAI automated deployment verification: ${FAILURES} failure(s)" >&2
exit 10
