package ai.weixiu.enumerate;

public enum ExtractionStatus {
    NOT_REQUESTED("NOT_REQUESTED"), PENDING("PENDING"), READY("READY"), FAILED("FAILED"), SUPERSEDED("SUPERSEDED");
    private final String value;
    ExtractionStatus(String value) { this.value = value; }
    public String getValue() { return value; }
}
