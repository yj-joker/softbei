package ai.weixiu.pojo;

import lombok.Data;

@Data
public class Result<T>{
    private String code;
    private String message;
    private T data;

    public static <T> Result<T>  success(T data){
        Result<T> result = new Result<>();
        result.setCode("200");
        result.setMessage("Ok");
        result.setData(data);
        return result;
    }

    public static  Result  success(){
        Result result = new Result<>();
        result.setCode("200");
        result.setMessage("Ok");
        return result;
    }

    public static Result error(String code,String message){
        Result result = new Result<>();
        result.setCode(code);
        result.setMessage(message);
        return result;
    }

}
