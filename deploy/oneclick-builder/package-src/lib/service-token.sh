#!/usr/bin/env bash
# Shared service-token contract for the FixAgent and Java services.

random_service_token() {
    openssl rand -hex 24
}

load_service_token_files() {
    local install_config="$1" secrets_file="$2"
    local inherited_api="${API_TOKEN:-}" inherited_internal="${INTERNAL_TOKEN:-}"
    local explicit_api explicit_internal
    if [[ -r "$install_config" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$install_config"
        set +a
    fi
    explicit_api="${API_TOKEN:-}"
    explicit_internal="${INTERNAL_TOKEN:-}"
    [[ -n "$explicit_api" ]] || explicit_api="$inherited_api"
    [[ -n "$explicit_internal" ]] || explicit_internal="$inherited_internal"
    if [[ -r "$secrets_file" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$secrets_file"
        set +a
    fi
    [[ -n "$explicit_api" ]] && API_TOKEN="$explicit_api"
    [[ -n "$explicit_internal" ]] && INTERNAL_TOKEN="$explicit_internal"
    export API_TOKEN INTERNAL_TOKEN
}

snapshot_service_env() {
    local env_file="$1" env_kind="$2"
    unset API_TOKEN INTERNAL_TOKEN AI_API_TOKEN SPRING_PROFILES_ACTIVE
    # shellcheck disable=SC1090
    source "$env_file"
    case "$env_kind" in
        fix)
            FIX_API_TOKEN="${API_TOKEN:-}"
            FIX_INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
            ;;
        java)
            JAVA_API_TOKEN="${AI_API_TOKEN:-}"
            JAVA_INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
            JAVA_PROFILE="${SPRING_PROFILES_ACTIVE:-}"
            ;;
        secrets)
            SECRET_API_TOKEN="${API_TOKEN:-}"
            SECRET_INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
            ;;
        *)
            printf '[MaintAI][错误] 未知环境快照类型\n' >&2
            return 1
            ;;
    esac
}

resolve_service_tokens() {
    local attempt generated_api generated_internal

    for attempt in 1 2 3 4 5; do
        if [[ -z "${API_TOKEN:-}" ]]; then
            generated_api="$(random_service_token)"
        else
            generated_api="$API_TOKEN"
        fi
        if [[ -z "${INTERNAL_TOKEN:-}" ]]; then
            generated_internal="$(random_service_token)"
        else
            generated_internal="$INTERNAL_TOKEN"
        fi
        if [[ -n "$generated_api" && -n "$generated_internal" && "$generated_api" != "$generated_internal" ]]; then
            API_TOKEN="$generated_api"
            INTERNAL_TOKEN="$generated_internal"
            export API_TOKEN INTERNAL_TOKEN
            return 0
        fi
        if [[ -n "${API_TOKEN:-}" && -n "${INTERNAL_TOKEN:-}" ]]; then
            printf '[MaintAI][错误] API_TOKEN与INTERNAL_TOKEN不能相同\n' >&2
            return 1
        fi
    done

    printf '[MaintAI][错误] 无法生成两枚不同的服务令牌\n' >&2
    return 1
}

render_service_token_envs() {
    local fixagent_env="$1" weixiu_env="$2"
    : "${API_TOKEN:?API_TOKEN未解析}"
    : "${INTERNAL_TOKEN:?INTERNAL_TOKEN未解析}"
    printf 'API_TOKEN=%s\nINTERNAL_TOKEN=%s\n' "$API_TOKEN" "$INTERNAL_TOKEN" >> "$fixagent_env"
    printf 'AI_API_TOKEN=%s\nINTERNAL_TOKEN=%s\n' "$API_TOKEN" "$INTERNAL_TOKEN" >> "$weixiu_env"
    chmod 0600 "$fixagent_env" "$weixiu_env"
}
