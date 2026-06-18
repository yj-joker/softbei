package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class UserVO {
    private Long id;
    private String username;
    private String name;
    private String number;
    private  Integer gender;
    private String phone;
    private LocalDateTime hireDate;
    private Integer  type;
    private Integer status;

}
