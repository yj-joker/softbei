package ai.weixiu.enumerate;

public enum ResolutionStatus {
    RESOLVED("RESOLVED"), PARTIALLY_RESOLVED("PARTIALLY_RESOLVED"), UNRESOLVED("UNRESOLVED");
    private final String value;
    ResolutionStatus(String value) { this.value = value; }
    public String getValue() { return value; }
    public static boolean isValid(String value) {
        for (ResolutionStatus status : values()) if (status.value.equals(value)) return true;
        return false;
    }
}
