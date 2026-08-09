package ai.weixiu.config;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DotEnvLoaderTest {

    @TempDir
    Path tempDirectory;

    @Test
    void parsesPortableDotenvSyntaxAsUtf8() throws Exception {
        Path envFile = tempDirectory.resolve(".env");
        Files.write(envFile, List.of(
                "\uFEFF# UTF-8 dotenv",
                "API_TOKEN=api-secret",
                "export INTERNAL_TOKEN='internal-secret'",
                "MINIO_ENDPOINT=localhost:9000 # local endpoint",
                "PASSWORD=abc#123",
                "EMPTY="
        ), StandardCharsets.UTF_8);

        DotEnvLoader.LoadedDotEnv loaded = DotEnvLoader.load(envFile);

        assertThat(loaded.path()).isEqualTo(envFile.toAbsolutePath().normalize());
        assertThat(loaded.properties()).containsEntry("API_TOKEN", "api-secret");
        assertThat(loaded.properties()).containsEntry("INTERNAL_TOKEN", "internal-secret");
        assertThat(loaded.properties()).containsEntry("MINIO_ENDPOINT", "localhost:9000");
        assertThat(loaded.properties()).containsEntry("PASSWORD", "abc#123");
        assertThat(loaded.properties()).containsEntry("EMPTY", "");
    }

    @Test
    void rejectsMalformedLinesWithTheirLineNumber() {
        assertThatThrownBy(() -> DotEnvLoader.parse(List.of(
                "API_TOKEN=valid",
                "not-an-assignment"
        )))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("line 2");
    }

    @Test
    void rejectsUnterminatedQuotes() {
        assertThatThrownBy(() -> DotEnvLoader.parse(List.of("API_TOKEN='broken")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("unterminated quoted value");
    }
}
