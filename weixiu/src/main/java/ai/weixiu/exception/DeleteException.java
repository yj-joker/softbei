package ai.weixiu.exception;
//删除错误异常
public class DeleteException extends RuntimeException {
    public DeleteException(String message) {
        super(message);
    }
}
