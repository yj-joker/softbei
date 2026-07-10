package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDate;
import java.time.LocalDateTime;

@EqualsAndHashCode(callSuper = true)
@Data
public class UserQuery extends PageQuery{
    private String name;
    private String number;
    private Integer gender;
    private String phone;
    private LocalDate hireDate;
}
