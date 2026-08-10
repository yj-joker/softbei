"""Static contract tests for the Java/FixAgent service-token configuration."""

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
JAVA_CONFIG = REPO_ROOT / "weixiu" / "src" / "main" / "resources" / "application.yml"
DOCKER_CONFIG = REPO_ROOT / "weixiu" / "src" / "main" / "resources" / "application-docker.yml"
ENV_EXAMPLE = REPO_ROOT / "FixAgent" / ".env.example"


def _env_value(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}=([^\r\n#]*)", text)
    assert match, f"{name} must be present in FixAgent/.env.example"
    return match.group(1).strip()


def test_application_binds_distinct_directional_tokens_without_defaults() -> None:
    text = JAVA_CONFIG.read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s*internal-token:\s*\$\{INTERNAL_TOKEN\}\s*$", text)
    assert re.search(
        r"(?m)^\s*api-token:\s*\$\{AI_API_TOKEN:\$\{API_TOKEN\}\}\s*$",
        text,
    )
    assert "api-token: ${AI_API_TOKEN:}" not in text
    assert "api-token: ${API_TOKEN:}" not in text
    assert "internal-token: ${INTERNAL_TOKEN:}" not in text
    assert "internal-token: fix-agent-internal-2026" not in text
    assert not re.search(r"(?m)^API_TOKEN\s*=\s*\$\{INTERNAL_TOKEN\}\s*$", text)
    assert "FixAgent→Java" in text
    assert "Java→FixAgent" in text
    assert "二者必须不同" in text


def test_docker_profile_inherits_token_contract() -> None:
    text = DOCKER_CONFIG.read_text(encoding="utf-8")

    assert not re.search(r"(?m)^\s*(?:internal-token|api-token):", text)
    assert "INTERNAL_TOKEN" not in text
    assert "AI_API_TOKEN" not in text


def test_env_example_declares_distinct_directional_tokens_and_java_url() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert _env_value(text, "API_TOKEN") == "YOUR_FIXAGENT_API_TOKEN"
    assert _env_value(text, "INTERNAL_TOKEN") == "YOUR_JAVA_INTERNAL_TOKEN"
    assert _env_value(text, "JAVA_SERVICE_URL") == "http://localhost:8080"
    assert _env_value(text, "API_TOKEN") != _env_value(text, "INTERNAL_TOKEN")
    assert "两个 token 必须非空、相互不同" in text
    assert "API_TOKEN=${INTERNAL_TOKEN}" not in text
