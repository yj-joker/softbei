package ai.weixiu.knowledge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.text.Normalizer;
import java.util.Locale;

/** Stable external identities for manual graph nodes and paths. */
public final class GraphStableIdentity {

    private GraphStableIdentity() {
    }

    public static String nodeId(
            String documentId,
            String documentVersion,
            String nodeType,
            String identityKey
    ) {
        String normalizedType = normalize(nodeType).toLowerCase(Locale.ROOT);
        String raw = String.join("\u001f",
                normalize(documentId),
                normalize(documentVersion).toLowerCase(Locale.ROOT),
                normalizedType,
                normalize(identityKey));
        return "kg:" + normalizedType + ":" + sha256(raw);
    }

    public static String pathId(String deviceStableId, String componentStableId, String faultStableId) {
        return "kgpath:" + sha256(String.join("\u001f",
                normalize(deviceStableId),
                normalize(componentStableId),
                normalize(faultStableId)));
    }

    public static String normalize(String value) {
        if (value == null) {
            return "";
        }
        return Normalizer.normalize(value, Normalizer.Form.NFKC)
                .trim()
                .replaceAll("\\s+", " ");
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder builder = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                builder.append(String.format("%02x", item));
            }
            return builder.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }
}
