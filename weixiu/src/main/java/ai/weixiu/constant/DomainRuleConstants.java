package ai.weixiu.constant;

public final class DomainRuleConstants {

    private DomainRuleConstants() {
    }

    public static final String STATUS_DRAFT = "draft";
    public static final String STATUS_PENDING = "pending";
    public static final String STATUS_ACTIVE = "active";
    public static final String STATUS_DISABLED = "disabled";
    public static final String STATUS_REJECTED = "rejected";

    public static final String SYNC_NOT_SYNCED = "not_synced";
    public static final String SYNC_SYNCING = "syncing";
    public static final String SYNC_SYNCED = "synced";
    public static final String SYNC_FAILED = "failed";

    public static String docId(Long ruleId) {
        return "domain_rule:" + ruleId;
    }
}
