package ai.weixiu.handler;



import ai.weixiu.exceprion.*;
import ai.weixiu.pojo.Result;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;


@RestControllerAdvice
@Slf4j
public class WebExceptionHandler {

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

    @ExceptionHandler(Exception.class)
    public Result handleException(Exception e) {
        return Result.error("500", e.getMessage());
    }
    @ExceptionHandler(UploadException.class)
    public Result handleUploadException(UploadException e) {
        return Result.error("500", e.getMessage());
    }
}
