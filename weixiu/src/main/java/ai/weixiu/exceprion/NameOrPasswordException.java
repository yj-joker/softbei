package ai.weixiu.exceprion;
/*
* 用户名或密码错误异常2
* */
public class NameOrPasswordException extends RuntimeException{
    public NameOrPasswordException(String message) {
        super(message);
    }
}
