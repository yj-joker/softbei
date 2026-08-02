#!/usr/bin/env bash
# Shared service-token contract for the FixAgent and Java services.

random_service_token() {
    openssl rand -hex 24
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
    cat >> "$fixagent_env" <<EOF
API_TOKEN=${API_TOKEN}
INTERNAL_TOKEN=${INTERNAL_TOKEN}
EOF
    cat >> "$weixiu_env" <<EOF
AI_API_TOKEN=${API_TOKEN}
INTERNAL_TOKEN=${INTERNAL_TOKEN}
EOF
}
