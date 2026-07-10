package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDate;
import java.time.LocalDateTime;

@Data
public class UserVO {
    private Long id;
    private String username;
    private String name;
    private String number;
    private  Integer gender;
    private String phone;
    private String email;
    private LocalDate hireDate;
    private Integer  type;
    private Integer status;
    private LocalDateTime lastLoginTime;

}
