package ai.weixiu.handler;



import ai.weixiu.exceprion.*;
import ai.weixiu.pojo.Result;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;


@RestControllerAdvice
@Slf4j
public class WebExceptionHandler {
    private static final String SERVER_ERROR_MESSAGE = "\u670d\u52a1\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5";

    @ExceptionHandler(NullException.class)
    public Result handler(NullException e) {
        log.info(e.getMessage());
        return Result.error("400", e.getMessage());
    }
    @ExceptionHandler(NameOrPasswordException.class)
    public Result handler(NameOrPasswordException e) {
        log.info(e.getMessage());
        return Result.error("401", e.getMessage());
    }

    @ExceptionHandler(ActivateException.class)
    public Result handler(ActivateException e) {
        log.info(e.getMessage());
        return Result.error("401", e.getMessage());
    }
    @ExceptionHandler(NotFoundException.class)
    public Result handler(NotFoundException e) {
        log.info(e.getMessage());
        return Result.error("404", e.getMessage());
    }
    @ExceptionHandler(EmailException.class)
    public Result handler(EmailException e) {
        log.info(e.getMessage());
        return Result.error("400", e.getMessage());
    }
    @ExceptionHandler(AiMemoryException.class)
    public Result handler(AiMemoryException e) {
        log.info(e.getMessage());
        return Result.error("400", e.getMessage());
    }
    @ExceptionHandler(EmbeddingException.class)
    public Result handler(EmbeddingException e) {
        log.info(e.getMessage());
        return Result.error("500", e.getMessage());
    }
    @ExceptionHandler(FormatErrorException.class)
    public Result handler(FormatErrorException e) {
        log.info(e.getMessage());
        return Result.error("400", e.getMessage());
    }
    @ExceptionHandler(IllegalArgumentException.class)
    public Result handleBadRequest(IllegalArgumentException e) {
        return Result.error("400", e.getMessage());
    }

    @ExceptionHandler(ForbiddenException.class)
    public Result handler(ForbiddenException e) {
        log.warn(e.getMessage());
        return Result.error("403", e.getMessage());
    }

    @ExceptionHandler(TaskStateException.class)
    public Result handler(TaskStateException e) {
        log.info(e.getMessage());
        return Result.error("409", e.getMessage());
    }

    @ExceptionHandler(DomainRuleSyncException.class)
    public Result handler(DomainRuleSyncException e) {
        log.warn(e.getMessage());
        return Result.error("502", e.getMessage());
    }

    @ExceptionHandler(Exception.class)
    public Result handleException(Exception e) {
        log.error("Unhandled server exception", e);
        return Result.error("500", SERVER_ERROR_MESSAGE);
    }
    @ExceptionHandler(UploadException.class)
    public Result handleUploadException(UploadException e) {
        log.error("Upload failed", e);
        return Result.error("500", SERVER_ERROR_MESSAGE);
    }
}
