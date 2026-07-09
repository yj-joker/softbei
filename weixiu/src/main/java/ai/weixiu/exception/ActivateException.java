package ai.weixiu.exceprion;

/*
* 未激活异常
* */
public class ActivateException extends RuntimeException {
    public ActivateException(String message) {
        super(message);
    }
}
