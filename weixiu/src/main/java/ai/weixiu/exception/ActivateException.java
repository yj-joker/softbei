package ai.weixiu.exception;

/*
* 未激活异常
* */
public class ActivateException extends RuntimeException {
    public ActivateException(String message) {
        super(message);
    }
}
