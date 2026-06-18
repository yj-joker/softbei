package ai.weixiu.entity;

import com.alibaba.excel.annotation.ExcelIgnore;
import com.alibaba.excel.annotation.ExcelProperty;
import com.alibaba.excel.annotation.format.DateTimeFormat;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * <p>
 * 用户表
 * </p>
 *
 * @author author
 * @since 2026-04-08
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = false)
@TableName("user")
public class User implements Serializable {

    private static final long serialVersionUID = 1L;

    /**
     * 主键
     */
    @TableId(value = "id", type = IdType.AUTO)
    @ExcelIgnore
    private Long id;

    /**
     * 身份证号，登录账号
     */
    @ExcelProperty(value = "身份证号")
    private String username;

    /**
     * 姓名
     */
    @ExcelProperty(value = "姓名")
    private String name;

    /**
     * 工号
     */
    @ExcelProperty(value = "工号")
    private String number;

    /**
     * bcrypt加密密码
     */
    @ExcelIgnore
    private String password;

    /**
     * 0=男, 1=女
     */
    @ExcelProperty(value = "性别")
    private Integer gender;

    /**
     * 0=员工, 1=管理员
     */
    @ExcelProperty(value = "用户类型")
    private Integer type;

    /**
     * 手机号
     */
    @ExcelProperty(value = "手机号")
    private String phone;
    /*
    * 邮箱
    * */
    @ExcelProperty(value = "邮箱")
    private String email;
    /**
     * 入职日期
     */
    @ExcelProperty(value = "入职日期")
    @DateTimeFormat("yyyy-MM-dd")
    private LocalDateTime hireDate;

    /**
     * 0=未激活, 1=已激活
     */
    @ExcelIgnore
    private Integer status;

    /**
     * 创建时间
     */
    @ExcelIgnore
    private LocalDateTime createTime;

    /**
     * 更新时间
     */
    @ExcelIgnore
    private LocalDateTime updateTime;

    /**
     * 最后登录时间
     */
    @ExcelIgnore
    private LocalDateTime lastLoginTime;
}
