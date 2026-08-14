package ai.weixiu.knowledge;

import java.util.List;

/** One immutable source tuple. Fields from distinct graph subjects must never be mixed. */
public record GraphPathProvenance(
        String documentId,
        String documentVersion,
        String sectionId,
        List<String> sourceChunkUids,
        Integer pageStart,
        Integer pageEnd,
        String graphRevision,
        String sourceSubjectType,
        String sourceSubjectStableId
) {
    public GraphPathProvenance {
        documentId = text(documentId);
        documentVersion = text(documentVersion);
        sectionId = text(sectionId);
        sourceChunkUids = sourceChunkUids == null
                ? List.of()
                : sourceChunkUids.stream().map(GraphPathProvenance::text)
                .filter(value -> !value.isBlank()).distinct().toList();
        graphRevision = text(graphRevision);
        sourceSubjectType = text(sourceSubjectType);
        sourceSubjectStableId = text(sourceSubjectStableId);
    }

    public boolean isComplete() {
        return !documentId.isBlank()
                && !documentVersion.isBlank()
                && !sectionId.isBlank()
                && !sourceChunkUids.isEmpty()
                && pageStart != null
                && !graphRevision.isBlank()
                && !sourceSubjectType.isBlank()
                && !sourceSubjectStableId.isBlank();
    }

    public static GraphPathProvenance select(
            boolean faultPath,
            GraphPathProvenance component,
            GraphPathProvenance fault
    ) {
        return faultPath ? fault : component;
    }

    private static String text(String value) {
        return value == null ? "" : value.trim();
    }
}
