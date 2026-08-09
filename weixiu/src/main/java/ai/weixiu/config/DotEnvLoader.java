package ai.weixiu.config;

import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Loads a dotenv file before Spring resolves application.yml placeholders.
 *
 * <p>The values are installed as Spring default properties. Operating-system
 * environment variables, JVM system properties and command-line arguments
 * therefore keep their normal higher precedence.</p>
 */
public final class DotEnvLoader {

    private static final Pattern KEY_PATTERN = Pattern.compile("[A-Za-z_][A-Za-z0-9_]*");

    private DotEnvLoader() {
    }

    public static Optional<LoadedDotEnv> loadDefault() {
        String configuredPath = firstNonBlank(
                System.getProperty("dotenv.file"),
                System.getenv("DOTENV_FILE")
        );
        if (configuredPath != null) {
            Path path = Paths.get(configuredPath).toAbsolutePath().normalize();
            if (!Files.isRegularFile(path)) {
                throw new IllegalStateException("Configured dotenv file does not exist: " + path);
            }
            return Optional.of(load(path));
        }

        for (Path candidate : defaultCandidates()) {
            if (Files.isRegularFile(candidate)) {
                return Optional.of(load(candidate));
            }
        }
        return Optional.empty();
    }

    public static LoadedDotEnv load(Path path) {
        Path normalized = path.toAbsolutePath().normalize();
        try {
            return new LoadedDotEnv(normalized, parse(Files.readAllLines(normalized, StandardCharsets.UTF_8)));
        } catch (IOException exception) {
            throw new IllegalStateException("Unable to read dotenv file: " + normalized, exception);
        }
    }

    static Map<String, Object> parse(Iterable<String> lines) {
        Map<String, Object> properties = new LinkedHashMap<>();
        int lineNumber = 0;
        for (String rawLine : lines) {
            lineNumber++;
            String line = rawLine == null ? "" : rawLine.trim();
            if (lineNumber == 1 && line.startsWith("\uFEFF")) {
                line = line.substring(1).trim();
            }
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            if (line.regionMatches(true, 0, "export ", 0, 7)) {
                line = line.substring(7).stripLeading();
            }

            int separator = line.indexOf('=');
            if (separator < 1) {
                throw parseError(lineNumber, "expected KEY=VALUE");
            }
            String key = line.substring(0, separator).trim();
            if (!KEY_PATTERN.matcher(key).matches()) {
                throw parseError(lineNumber, "invalid key: " + key);
            }

            String value = line.substring(separator + 1).trim();
            if (!value.isEmpty() && (value.charAt(0) == '\"' || value.charAt(0) == '\'')) {
                char quote = value.charAt(0);
                if (value.length() < 2 || value.charAt(value.length() - 1) != quote) {
                    throw parseError(lineNumber, "unterminated quoted value");
                }
                value = value.substring(1, value.length() - 1);
            } else {
                value = value.replaceFirst("\\s+#.*$", "").trim();
            }
            properties.put(key, value);
        }
        return properties;
    }

    private static Set<Path> defaultCandidates() {
        Set<Path> candidates = new LinkedHashSet<>();
        Path workingDirectory = Paths.get("").toAbsolutePath().normalize();
        candidates.add(workingDirectory.resolve(".env"));
        if (workingDirectory.getParent() != null) {
            candidates.add(workingDirectory.getParent().resolve(".env"));
        }

        applicationDirectory().ifPresent(directory -> {
            candidates.add(directory.resolve(".env"));
            if (directory.getParent() != null) {
                candidates.add(directory.getParent().resolve(".env"));
            }
        });
        return candidates;
    }

    private static Optional<Path> applicationDirectory() {
        try {
            URI location = DotEnvLoader.class.getProtectionDomain().getCodeSource().getLocation().toURI();
            Path path = Paths.get(location).toAbsolutePath().normalize();
            return Optional.ofNullable(Files.isRegularFile(path) ? path.getParent() : null);
        } catch (Exception ignored) {
            return Optional.empty();
        }
    }

    private static IllegalArgumentException parseError(int lineNumber, String detail) {
        return new IllegalArgumentException("Invalid dotenv line " + lineNumber + ": " + detail);
    }

    private static String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }

    public record LoadedDotEnv(Path path, Map<String, Object> properties) {
        public LoadedDotEnv {
            properties = Map.copyOf(properties);
        }
    }
}
