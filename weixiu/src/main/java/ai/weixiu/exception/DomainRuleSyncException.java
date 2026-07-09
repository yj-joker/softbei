package ai.weixiu.exception;

public class DomainRuleSyncException extends RuntimeException {
    public DomainRuleSyncException(String message) {
        super(message);
    }

    public DomainRuleSyncException(String message, Throwable cause) {
        super(message, cause);
    }
}
