#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FIX_TEMPLATE="$ROOT/../../package_templates/conf/fixagent.env.example"
JAVA_TEMPLATE="$ROOT/../../package_templates/conf/weixiu.env.example"
INSTALL_CONFIG="$ROOT/config/install.env"
INSTALLER="$ROOT/install.sh"
VERIFY="$ROOT/verify.sh"
HELPER="$ROOT/lib/service-token.sh"
FAILURES=0

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }
contains() { grep -Fq -- "$2" "$1"; }
not_contains() { ! grep -Fq -- "$2" "$1"; }
assert_contains() {
    local file="$1" text="$2" name="$3"
    contains "$file" "$text" && pass "$name" || fail "$name"
}
assert_not_contains() {
    local file="$1" text="$2" name="$3"
    not_contains "$file" "$text" && pass "$name" || fail "$name"
}

assert_contains "$INSTALL_CONFIG" 'API_TOKEN=' 'install.env声明API_TOKEN'
assert_contains "$INSTALL_CONFIG" 'INTERNAL_TOKEN=' 'install.env声明INTERNAL_TOKEN'
assert_contains "$FIX_TEMPLATE" 'API_TOKEN=<FIXAGENT_API_TOKEN>' 'FixAgent模板声明API_TOKEN方向'
assert_contains "$FIX_TEMPLATE" 'INTERNAL_TOKEN=<JAVA_INTERNAL_TOKEN>' 'FixAgent模板声明INTERNAL_TOKEN方向'
assert_contains "$JAVA_TEMPLATE" 'AI_API_TOKEN=<FIXAGENT_API_TOKEN>' 'Java模板声明AI_API_TOKEN方向'
assert_contains "$JAVA_TEMPLATE" 'INTERNAL_TOKEN=<JAVA_INTERNAL_TOKEN>' 'Java模板声明INTERNAL_TOKEN方向'
assert_not_contains "$FIX_TEMPLATE" '<INTERNAL_API_TOKEN>' 'FixAgent模板不复用旧占位符'
assert_not_contains "$JAVA_TEMPLATE" '<INTERNAL_API_TOKEN>' 'Java模板不复用旧占位符'
assert_contains "$INSTALLER" 'source "$PACKAGE_ROOT/lib/service-token.sh"' '安装器加载共享令牌helper'
assert_contains "$INSTALLER" 'load_service_token_files "$INSTALL_CONFIG" "$SECRETS_FILE"' '安装器按显式输入优先加载令牌'
assert_contains "$INSTALLER" 'resolve_service_tokens' '安装器调用双令牌解析'
assert_contains "$INSTALLER" 'render_service_token_envs "$CONFIG_ROOT/fixagent.env" "$CONFIG_ROOT/weixiu.env"' '安装器调用实际共享渲染路径'
assert_not_contains "$INSTALLER" 'API_TOKEN=${INTERNAL_TOKEN}' '安装器不再单令牌映射'
assert_contains "$INSTALLER" 'SPRING_PROFILES_ACTIVE=prod' '安装器生产profile为prod'
assert_contains "$VERIFY" 'AI_API_TOKEN' 'verify检查Java API令牌'
assert_contains "$VERIFY" 'INTERNAL_TOKEN' 'verify检查internal令牌'
assert_contains "$VERIFY" 'SPRING_PROFILES_ACTIVE' 'verify检查Java profile'
assert_contains "$VERIFY" 'JAVA_PROFILE' 'verify使用Java profile快照'
assert_contains "$VERIFY" 'snapshot_service_env "$FIX_ENV" fix' 'verify隔离FixAgent环境快照'
assert_contains "$VERIFY" 'snapshot_service_env "$JAVA_ENV" java' 'verify隔离Java环境快照'
assert_contains "$VERIFY" 'snapshot_service_env "$SECRETS_ENV" secrets' 'verify隔离安装密钥快照'
assert_not_contains "$VERIFY" 'echo "$API_TOKEN"' 'verify不直接输出API令牌'
assert_not_contains "$VERIFY" 'echo "$INTERNAL_TOKEN"' 'verify不直接输出internal令牌'

if bash -c 'set -Eeuo pipefail; source "$1"; [[ -z "${MYSQL_PASSWORD:-}" && -z "${DASHSCOPE_API_KEY:-}" && -z "${API_TOKEN:-}" && -z "${INTERNAL_TOKEN:-}" ]]' _ "$INSTALL_CONFIG"; then
    pass 'install.env可在严格模式下安全source且秘密为空'
else
    fail 'install.env可在严格模式下安全source且秘密为空'
fi

if [[ ! -r "$HELPER" ]]; then
    fail '共享令牌helper存在且可读取'
else
    # shellcheck disable=SC1090
    source "$HELPER"
    tmpdir="$(mktemp -d)"
    trap 'rm -rf -- "$tmpdir"' EXIT

    API_TOKEN=''
    INTERNAL_TOKEN=''
    resolve_service_tokens
    [[ -n "$API_TOKEN" && -n "$INTERNAL_TOKEN" && "$API_TOKEN" != "$INTERNAL_TOKEN" ]] \
        && pass '两值为空时分别生成非空且不同令牌' || fail '两值为空时分别生成非空且不同令牌'

    API_TOKEN='api-test-value'
    INTERNAL_TOKEN=''
    resolve_service_tokens
    [[ "$API_TOKEN" == 'api-test-value' && -n "$INTERNAL_TOKEN" && "$API_TOKEN" != "$INTERNAL_TOKEN" ]] \
        && pass '仅API给定时保留API并生成internal' || fail '仅API给定时保留API并生成internal'

    API_TOKEN=''
    INTERNAL_TOKEN='internal-test-value'
    resolve_service_tokens
    [[ -n "$API_TOKEN" && "$INTERNAL_TOKEN" == 'internal-test-value' && "$API_TOKEN" != "$INTERNAL_TOKEN" ]] \
        && pass '仅internal给定时生成API并保留internal' || fail '仅internal给定时生成API并保留internal'

    API_TOKEN='api-test-value'
    INTERNAL_TOKEN='internal-test-value'
    resolve_service_tokens
    [[ "$API_TOKEN" == 'api-test-value' && "$INTERNAL_TOKEN" == 'internal-test-value' ]] \
        && pass '两个不同给定值原样保留' || fail '两个不同给定值原样保留'

    old_api='old-api-value'
    old_internal='old-internal-value'
    explicit_api='explicit-api-value'
    explicit_internal='explicit-internal-value'
    priority_output="$tmpdir/priority-output"
    if bash -c 'set -Eeuo pipefail; source "$1"; API_TOKEN="$2"; INTERNAL_TOKEN="$3"; load_service_token_files "$4" "$5"; [[ "$API_TOKEN" == "$2" && "$INTERNAL_TOKEN" == "$3" ]]' _ "$HELPER" "$explicit_api" "$explicit_internal" "$tmpdir/missing-install.env" "$tmpdir/old-secrets.env" >"$priority_output" 2>&1; then
        pass '重装时显式API和internal优先于旧密钥'
    else
        fail '重装时显式API和internal优先于旧密钥'
    fi
    printf 'API_TOKEN=%s\nINTERNAL_TOKEN=%s\n' "$old_api" "$old_internal" > "$tmpdir/old-secrets.env"
    if bash -c 'set -Eeuo pipefail; source "$1"; API_TOKEN="$2"; INTERNAL_TOKEN=""; load_service_token_files "$3" "$4"; [[ "$API_TOKEN" == "$2" && "$INTERNAL_TOKEN" != "$4" ]]' _ "$HELPER" "$explicit_api" "$tmpdir/missing-install.env" "$tmpdir/old-secrets.env"; then
        pass '重装时显式API覆盖旧API且补生成internal'
    else
        fail '重装时显式API覆盖旧API且补生成internal'
    fi
    if bash -c 'set -Eeuo pipefail; source "$1"; API_TOKEN=""; INTERNAL_TOKEN="$2"; load_service_token_files "$3" "$4"; [[ "$INTERNAL_TOKEN" == "$2" && "$API_TOKEN" != "$3" ]]' _ "$HELPER" "$explicit_internal" "$tmpdir/missing-install.env" "$tmpdir/old-secrets.env"; then
        pass '重装时显式internal覆盖旧internal且补生成API'
    else
        fail '重装时显式internal覆盖旧internal且补生成API'
    fi
    same_priority_output="$tmpdir/same-priority-output"
    if bash -c 'set -Eeuo pipefail; source "$1"; API_TOKEN=same-explicit-value; INTERNAL_TOKEN=same-explicit-value; load_service_token_files "$2" "$3"; resolve_service_tokens' _ "$HELPER" "$tmpdir/missing-install.env" "$tmpdir/old-secrets.env" >"$same_priority_output" 2>&1; then
        fail '重装显式相同令牌失败'
    elif grep -Fq 'same-explicit-value' "$same_priority_output"; then
        fail '重装显式相同令牌失败信息不泄露值'
    else
        pass '重装显式相同令牌失败且不泄露值'
    fi

    same_output="$tmpdir/same-output"
    if bash -c 'set -Eeuo pipefail; source "$1"; API_TOKEN=same-test-value; INTERNAL_TOKEN=same-test-value; resolve_service_tokens' _ "$HELPER" >"$same_output" 2>&1; then
        fail '两个相同给定值fail-fast'
    elif grep -Fq 'same-test-value' "$same_output"; then
        fail '相同令牌失败信息不泄露值'
    else
        pass '两个相同给定值fail-fast且不泄露值'
    fi

    collision_output="$tmpdir/collision-output"
    collision_state="$tmpdir/collision-state"
    if bash -c 'set -Eeuo pipefail; source "$1"; random_service_token() { printf collision-value; }; API_TOKEN=""; INTERNAL_TOKEN=""; resolve_service_tokens; printf "%s\n%s\n" "${API_TOKEN:-}" "${INTERNAL_TOKEN:-}" > "$2"' _ "$HELPER" "$collision_state" >"$collision_output" 2>&1; then
        fail '连续随机碰撞最终失败'
    elif grep -Fq 'collision-value' "$collision_output" || [[ -s "$collision_state" ]]; then
        fail '随机碰撞失败不泄露或产生生成值'
    else
        pass '连续随机碰撞最终失败且不产生令牌输出'
    fi

    API_TOKEN='api-render-value'
    INTERNAL_TOKEN='internal-render-value'
    : > "$tmpdir/fixagent.env"
    : > "$tmpdir/weixiu.env"
    render_service_token_envs "$tmpdir/fixagent.env" "$tmpdir/weixiu.env"
    chmod 0600 "$tmpdir/fixagent.env" "$tmpdir/weixiu.env"
    [[ -f "$tmpdir/fixagent.env" && -f "$tmpdir/weixiu.env" ]] \
        && pass '临时渲染文件权限由共享helper写入合同' || fail '临时渲染文件权限由共享helper写入合同'

    if bash -c 'set -Eeuo pipefail; source "$1"; API_TOKEN=same-test-value; INTERNAL_TOKEN=same-test-value; resolve_service_tokens' _ "$HELPER" >"$same_output" 2>&1; then
        fail '两个相同给定值fail-fast'
    elif grep -Fq 'same-test-value' "$same_output"; then
        fail '相同令牌失败信息不泄露值'
    else
        pass '两个相同给定值fail-fast且不泄露值'
    fi

    API_TOKEN='api-render-value'
    INTERNAL_TOKEN='internal-render-value'
    render_service_token_envs "$tmpdir/fixagent.env" "$tmpdir/weixiu.env"
    assert_contains "$tmpdir/fixagent.env" 'API_TOKEN=api-render-value' '实际FixAgent渲染API方向'
    assert_contains "$tmpdir/fixagent.env" 'INTERNAL_TOKEN=internal-render-value' '实际FixAgent渲染internal方向'
    assert_contains "$tmpdir/weixiu.env" 'AI_API_TOKEN=api-render-value' '实际Java渲染API方向'
    assert_contains "$tmpdir/weixiu.env" 'INTERNAL_TOKEN=internal-render-value' '实际Java渲染internal方向'
fi

if [[ "$FAILURES" -eq 0 ]]; then
    printf 'service-token-contract: ALL PASS\n'
    exit 0
fi
printf 'service-token-contract: %d failure(s)\n' "$FAILURES" >&2
exit 1
