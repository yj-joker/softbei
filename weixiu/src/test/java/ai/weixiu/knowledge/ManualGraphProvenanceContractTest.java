package ai.weixiu.knowledge;

import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ManualGraphProvenanceContractTest {

    @Test
    void deviceMergeMustIncludeDocumentScopedIdentity() throws Exception {
        String source = Files.readString(Path.of(
                "src/main/java/ai/weixiu/controller/ManualKGInternalController.java"));

        assertTrue(source.contains(
                "MERGE (d:Device {document_id: $documentId, identity_key: $identityKey})"));
        assertFalse(source.contains("MERGE (d:Device {name: $name})"));
        assertTrue(source.contains("d.section_id"));
        assertTrue(source.contains("d.source_chunk_uid"));
    }
}
