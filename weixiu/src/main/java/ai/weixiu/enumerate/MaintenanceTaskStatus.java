package ai.weixiu.enumerate;

public enum MaintenanceTaskStatus {
    RESOLUTION_PENDING("RESOLUTION_PENDING");
    private final String value;
    MaintenanceTaskStatus(String value) { this.value = value; }
    public String getValue() { return value; }
}
