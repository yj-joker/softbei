#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FIX_TEMPLATE="$ROOT/../../package_templates/conf/fixagent.env.example"
JAVA_TEMPLATE="$ROOT/../../package_templates/conf/weixiu.env.example"
INSTALL_CONFIG="$ROOT/config/install.env"
INSTALLER="$ROOT/install.sh"
VERIFY="$ROOT/verify.sh"
PRODUCTION_HELPER="${SERVICE_TOKEN_HELPER_UNDER_TEST:-$ROOT/lib/service-token.sh}"
HELPER="$PRODUCTION_HELPER"
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
    ! contains "$file" "$text" && pass "$name" || fail "$name"
}
mode_matches() {
    local actual="$1" expected="$2" platform="$3"
    case "$platform" in
        msys*|cygwin*|win32*)
            [[ "$actual" == '644' && ( "$expected" == '600' || "$expected" == '640' || "$expected" == '644' ) ]]
            ;;
        *)
            [[ "$actual" == "$expected" ]]
            ;;
    esac
}
assert_mode() {
    local file="$1" expected="$2" name="$3" actual
    actual="$(stat -c '%a' "$file")"
    if mode_matches "$actual" "$expected" "$OSTYPE"; then
        if [[ "$actual" == "$expected" ]]; then
            pass "$name"
        else
            pass "$name（Windows Git Bash stat仅验证644兼容映射）"
        fi
    else
        fail "$name（实际${actual}，期望${expected}）"
    fi
}

if mode_matches '755' '644' 'msys'; then
    fail 'Windows动态stat兼容不得将755映射为期望644'
else
    pass 'Windows动态stat兼容明确拒绝755映射为期望644'
fi

assert_contains "$INSTALL_CONFIG" 'API_TOKEN=' 'install.env声明API_TOKEN'
assert_contains "$INSTALL_CONFIG" 'INTERNAL_TOKEN=' 'install.env声明INTERNAL_TOKEN'
assert_contains "$INSTALL_CONFIG" 'API_TOKEN和INTERNAL_TOKEN均留空时安装器会分别生成两枚不同令牌' 'install.env明确空令牌分别生成且不同'
assert_contains "$FIX_TEMPLATE" 'API_TOKEN=<FIXAGENT_API_TOKEN>' 'FixAgent模板声明API_TOKEN方向'
assert_contains "$FIX_TEMPLATE" 'INTERNAL_TOKEN=<JAVA_INTERNAL_TOKEN>' 'FixAgent模板声明INTERNAL_TOKEN方向'
assert_contains "$JAVA_TEMPLATE" 'AI_API_TOKEN=<FIXAGENT_API_TOKEN>' 'Java模板声明AI_API_TOKEN方向'
assert_contains "$JAVA_TEMPLATE" 'INTERNAL_TOKEN=<JAVA_INTERNAL_TOKEN>' 'Java模板声明INTERNAL_TOKEN方向'
assert_not_contains "$FIX_TEMPLATE" '<INTERNAL_API_TOKEN>' 'FixAgent模板不复用旧占位符'
assert_not_contains "$JAVA_TEMPLATE" '<INTERNAL_API_TOKEN>' 'Java模板不复用旧占位符'
assert_contains "$INSTALLER" 'copy_runtime_service_helper "$PACKAGE_ROOT/lib/service-token.sh" "$APP_STAGE/lib/service-token.sh"' '安装器通过共享helper复制运行时helper'
assert_contains "$INSTALLER" 'write_service_token_secrets "$SECRETS_FILE"' '安装器通过共享helper写入install secrets'
assert_contains "$INSTALLER" 'mkdir -p "$APP_STAGE/lib"' '安装器创建运行时helper目录'
assert_contains "$INSTALLER" 'chmod 0640 "$CONFIG_ROOT/fixagent.env"' '安装器FixAgent服务env权限为0640'
assert_contains "$INSTALLER" 'chmod 0640 "$CONFIG_ROOT/weixiu.env"' '安装器Java服务env权限为0640'
assert_contains "$PRODUCTION_HELPER" 'chmod 0600 "$secrets_file"' 'Windows动态stat局限由生产helper静态0600语句补强'
assert_contains "$PRODUCTION_HELPER" 'chmod 0640 "$fixagent_env" "$weixiu_env"' 'Windows动态stat局限由生产helper静态0640语句补强'
assert_contains "$PRODUCTION_HELPER" 'chmod 0644 "$target_file"' 'Windows动态stat局限由生产helper静态0644语句补强'
assert_contains "$INSTALLER" 'source "$PACKAGE_ROOT/lib/service-token.sh"' '安装器加载共享令牌helper'
assert_contains "$INSTALLER" 'load_service_token_files "$INSTALL_CONFIG" "$SECRETS_FILE"' '安装器按显式输入优先加载令牌'
assert_contains "$INSTALLER" 'resolve_service_tokens' '安装器调用双令牌解析'
assert_contains "$INSTALLER" 'render_service_token_envs "$CONFIG_ROOT/fixagent.env" "$CONFIG_ROOT/weixiu.env"' '安装器调用实际共享渲染路径'
assert_not_contains "$INSTALLER" 'API_TOKEN=${INTERNAL_TOKEN}' '安装器不再单令牌映射'
assert_contains "$INSTALLER" 'SPRING_PROFILES_ACTIVE=' '安装器使用默认Spring配置且不强制dev profile'
assert_contains "$VERIFY" 'AI_API_TOKEN' 'verify检查Java API令牌'
assert_contains "$VERIFY" 'INTERNAL_TOKEN' 'verify检查internal令牌'
assert_contains "$VERIFY" 'Java使用默认Spring配置' 'verify检查Java默认配置'
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
    printf 'API_TOKEN=%s\nINTERNAL_TOKEN=%s\n' "$old_api" "$old_internal" > "$tmpdir/old-secrets.env"
    printf 'API_TOKEN=%s\nINTERNAL_TOKEN=%s\n' "$explicit_api" "$explicit_internal" > "$tmpdir/explicit-install.env"
    priority_output="$tmpdir/priority-output"
    if bash -c 'set -Eeuo pipefail; source "$1"; load_service_token_files "$2" "$3"; [[ "$API_TOKEN" == "$4" && "$INTERNAL_TOKEN" == "$5" ]]' _ "$HELPER" "$tmpdir/explicit-install.env" "$tmpdir/old-secrets.env" "$explicit_api" "$explicit_internal" >"$priority_output" 2>&1; then
        pass '重装时显式API和internal优先于旧密钥'
    else
        fail '重装时显式API和internal优先于旧密钥'
    fi
    printf 'API_TOKEN=%s\n' "$explicit_api" > "$tmpdir/explicit-api-install.env"
    if env -u API_TOKEN -u INTERNAL_TOKEN bash -c 'set -Eeuo pipefail; source "$1"; load_service_token_files "$2" "$3"; [[ "$API_TOKEN" == "$4" && "$INTERNAL_TOKEN" == "$5" ]]' _ "$HELPER" "$tmpdir/explicit-api-install.env" "$tmpdir/old-secrets.env" "$explicit_api" "$old_internal"; then
        pass '重装仅显式API时internal严格沿用旧密钥'
    else
        fail '重装仅显式API时internal严格沿用旧密钥'
    fi
    printf 'INTERNAL_TOKEN=%s\n' "$explicit_internal" > "$tmpdir/explicit-internal-install.env"
    if env -u API_TOKEN -u INTERNAL_TOKEN bash -c 'set -Eeuo pipefail; source "$1"; load_service_token_files "$2" "$3"; [[ "$API_TOKEN" == "$4" && "$INTERNAL_TOKEN" == "$5" ]]' _ "$HELPER" "$tmpdir/explicit-internal-install.env" "$tmpdir/old-secrets.env" "$old_api" "$explicit_internal"; then
        pass '重装仅显式internal时API严格沿用旧密钥'
    else
        fail '重装仅显式internal时API严格沿用旧密钥'
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

    fix_env="$tmpdir/snapshot-fix.env"
    java_env="$tmpdir/snapshot-java.env"
    secrets_env="$tmpdir/snapshot-secrets.env"
    printf 'INTERNAL_TOKEN=file-fix-internal\n' > "$fix_env"
    printf 'INTERNAL_TOKEN=file-java-internal\nSPRING_PROFILES_ACTIVE=prod\n' > "$java_env"
    printf 'INTERNAL_TOKEN=file-secrets-internal\n' > "$secrets_env"
    API_TOKEN=external-api INTERNAL_TOKEN=external-internal AI_API_TOKEN=external-java SPRING_PROFILES_ACTIVE=dev
    snapshot_service_env "$fix_env" fix
    snapshot_service_env "$java_env" java
    snapshot_service_env "$secrets_env" secrets
    [[ -z "$FIX_API_TOKEN" && "$FIX_INTERNAL_TOKEN" == 'file-fix-internal' ]] \
        && pass '实际FixAgent快照缺字段且不受外部污染' || fail '实际FixAgent快照缺字段或受外部污染'
    [[ -z "$JAVA_API_TOKEN" && "$JAVA_INTERNAL_TOKEN" == 'file-java-internal' && "$JAVA_PROFILE" == 'prod' ]] \
        && pass '实际Java快照缺API且不受外部污染' || fail '实际Java快照缺API或受外部污染'
    [[ -z "$SECRET_API_TOKEN" && "$SECRET_INTERNAL_TOKEN" == 'file-secrets-internal' ]] \
        && pass '实际secrets快照缺API且不受外部污染' || fail '实际secrets快照缺API或受外部污染'

    API_TOKEN='api-render-value'
    INTERNAL_TOKEN='internal-render-value'
    : > "$tmpdir/fixagent.env"
    : > "$tmpdir/weixiu.env"
    render_service_token_envs "$tmpdir/fixagent.env" "$tmpdir/weixiu.env"
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
    assert_mode "$tmpdir/fixagent.env" '640' '实际渲染FixAgent服务env权限为640'
    assert_mode "$tmpdir/weixiu.env" '640' '实际渲染Java服务env权限为640'

    app_stage="$tmpdir/app-stage"
    mkdir -p "$app_stage/lib"
    copy_runtime_service_helper "$HELPER" "$app_stage/lib/service-token.sh"
    case "$OSTYPE" in
        msys*|cygwin*|win32*)
            pass 'Windows Git Bash脚本动态stat无法表达生产helper的0644，以静态chmod语句补强且不接受755映射'
            ;;
        *)
            assert_mode "$app_stage/lib/service-token.sh" '644' '非Windows精确验证运行时helper复制权限为644'
            ;;
    esac

    install_secrets="$tmpdir/install-secrets.env"
    printf 'OTHER=value\n' > "$install_secrets"
    API_TOKEN='written-api-value'
    INTERNAL_TOKEN='written-internal-value'
    write_service_token_secrets "$install_secrets"
    assert_contains "$install_secrets" 'OTHER=value' '实际install-secrets写入保留其他非token配置'
    assert_contains "$install_secrets" 'API_TOKEN=written-api-value' '实际install-secrets写入API令牌'
    assert_contains "$install_secrets" 'INTERNAL_TOKEN=written-internal-value' '实际install-secrets写入internal令牌'
    assert_mode "$install_secrets" '600' '实际调用write_service_token_secrets后权限为600'

    collision_secrets="$tmpdir/collision-secrets.env"
    collision_before="$tmpdir/collision-secrets.before"
    printf 'OTHER=preserved\nAPI_TOKEN=old-api\nINTERNAL_TOKEN=old-internal\n' > "$collision_secrets"
    cp "$collision_secrets" "$collision_before"
    collision_output="$tmpdir/collision-output"
    if bash -c 'set -Eeuo pipefail; source "$1"; random_service_token() { printf collision-value; }; API_TOKEN=""; INTERNAL_TOKEN=""; resolve_service_tokens; write_service_token_secrets "$2"' _ "$HELPER" "$collision_secrets" >"$collision_output" 2>&1; then
        fail '连续随机碰撞命令独立断言失败'
    else
        pass '连续随机碰撞命令独立断言失败'
    fi
    if grep -Fq 'collision-value' "$collision_output"; then
        fail '连续随机碰撞输出独立断言不含碰撞值'
    else
        pass '连续随机碰撞输出独立断言不含碰撞值'
    fi
    if cmp -s "$collision_before" "$collision_secrets"; then
        pass '连续随机碰撞前后secrets逐字节完全一致'
    else
        fail '连续随机碰撞前后secrets逐字节完全一致'
    fi
fi

if [[ "$FAILURES" -eq 0 ]]; then
    printf 'service-token-contract: ALL PASS\n'
    exit 0
fi
printf 'service-token-contract: %d failure(s)\n' "$FAILURES" >&2
exit 1
