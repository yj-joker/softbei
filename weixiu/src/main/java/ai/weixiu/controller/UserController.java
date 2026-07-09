package ai.weixiu.controller;


import ai.weixiu.annotation.OpLog;
import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.UserDTO;
import ai.weixiu.pojo.dto.UserLoginDTO;
import ai.weixiu.pojo.query.UserQuery;
import ai.weixiu.pojo.vo.BatchRegisterResultVO;
import ai.weixiu.pojo.vo.UserVO;
import ai.weixiu.service.MioIOUpLoadService;
import ai.weixiu.service.UserService;
import ai.weixiu.utils.AliyunUpLoadUtils;
import com.alibaba.excel.EasyExcel;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import lombok.AllArgsConstructor;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;

/**
 * <p>
 * 用户表 前端控制器
 * </p>
 *
 * @author author
 * @since 2026-04-08
 */
@RestController
@RequestMapping("/weixiu/user")
@AllArgsConstructor
@Tag(name = "用户管理")
public class UserController {
    private final UserService userService;
    private final AliyunUpLoadUtils aliyunUpLoadUtils;
    private final MioIOUpLoadService minIOUpLoadService;

    /*
     * Excel 批量注册用户（仅管理员）
     * */
    @PostMapping(value = "/register", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @Operation(summary = "Excel批量注册用户")
    @RequireAdmin
    public Result<BatchRegisterResultVO> batchRegister(@RequestParam("file") MultipartFile file) {
        return Result.success(userService.batchRegister(file));
    }

    /*
     * 下载批量注册 Excel 模板（仅管理员）
     * 表头须与 User 的 @ExcelProperty 一致；用户类型由后端强制为员工，故模板不含该列
     * */
    @GetMapping("/register-template")
    @Operation(summary = "下载批量注册Excel模板")
    @RequireAdmin
    public void downloadRegisterTemplate(HttpServletResponse response) throws IOException {
        response.setContentType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
        response.setCharacterEncoding("utf-8");
        String fileName = URLEncoder.encode("用户批量导入模板", StandardCharsets.UTF_8).replaceAll("\\+", "%20");
        response.setHeader("Content-disposition", "attachment;filename*=utf-8''" + fileName + ".xlsx");

        List<List<String>> head = Arrays.asList(
                List.of("身份证号"), List.of("姓名"), List.of("工号"),
                List.of("性别"), List.of("手机号"), List.of("邮箱"), List.of("入职日期"));
        // 示例行：性别填 0(男)/1(女)，入职日期 yyyy-MM-dd
        List<List<Object>> sample = List.of(Arrays.asList(
                (Object) "110101199001011234", "张三", "G001", 0, "13800138000", "zhangsan@example.com", "2024-01-15"));

        EasyExcel.write(response.getOutputStream()).head(head).sheet("用户导入").doWrite(sample);
    }

    /*
     * 用户登录
     * */
    @PostMapping("/login")
    @Operation(summary = "用户登录")
    public Result login(@Valid @RequestBody UserLoginDTO userLoginDTO, HttpServletRequest httpRequest) {
        UserVO userVO = userService.login(userLoginDTO, httpRequest);
        return Result.success(userVO);
    }

    /*
     * 根据用户id查询用户信息
     * */
    @PostMapping("/getUserById")
    @Operation(summary = "根据用户id查询用户信息")
    public Result getUserById(Integer id) {
        UserVO userVO = userService.getUserById(id);
        return Result.success(userVO);
    }

    /*
     * 根据用户id批量删除用户
     * */
    @DeleteMapping("/deleteByIds")
    @Operation(summary = "根据用户id批量删除用户")
    public Result deleteByIds(@RequestBody List<Integer> ids) {
        userService.removeUserByIds(ids);
        return Result.success();
    }
    /*
    * 根据用户id修改用户信息
    * */
    @PutMapping("/updateUser")
    @Operation(summary = "修改用户信息")
    @OpLog(value = "更新了用户信息", targetType = "user")
    public Result updateById(@RequestBody UserDTO userDTO) {
        userService.updateUser(userDTO);
        return Result.success();
    }
    /*
     * 分页查询所有用户
     * */
    @PostMapping("/list")
    @Operation(summary = "分页查询所有用户")
    public Result<List<UserVO>> list(@RequestBody UserQuery userQuery) {
        List<UserVO> userVOList = userService.getUserList(userQuery);
        return Result.success(userVOList);
    }
    /*
    * 向邮箱发送验证码
    * */
    @PostMapping("/sendEmail")
    @Operation(summary = "向邮箱发送验证码")
    public Result sendEmail(String email,Integer mode) {
        userService.sendEmail(email, mode);
        return Result.success();
    }
    /*
    * 验证验证码,并修改密码或绑定邮箱
    * */
    @PostMapping("/verifyEmail")
    @Operation(summary = "验证验证码")
    public Result verifyEmail(String code,Integer mode,String  emailOrPassword) {
        userService.verifyEmail(code, mode,emailOrPassword);
        return Result.success();
    }
    /*
    * 阿里云OSS上传图片
    * */
    @PostMapping("/uploadByAliyun")
    @Operation(summary = "上传图片")
    public Result uploadByAliyun(MultipartFile file)  {
        String url;
        try {
            url = aliyunUpLoadUtils.upload(file);
        } catch (IOException e) {
           return Result.error("500","上传失败");
        }
        return Result.success(url);
    }
    /**
     * 本地MinIO上传图片上传文件
     */
    @PostMapping("/uploadByMinIO")
    public Result<String> uploadByMinIO(@RequestParam("file") MultipartFile file,BucketEnum bucket) {
    return Result.success( minIOUpLoadService.upload(file, bucket));
    }

    /**
     * 下载文件
     * GET /api/file/download?objectName=xxx.jpg
     */
    @GetMapping("/download")
    public ResponseEntity<byte[]> download(@RequestParam String objectName, BucketEnum bucket) {
        try (InputStream is = minIOUpLoadService.download(objectName,bucket)) {
            byte[] bytes = is.readAllBytes();
            return ResponseEntity.ok()
                    .header(HttpHeaders.CONTENT_DISPOSITION,
                            "attachment; filename=\"" + objectName + "\"")
                    .contentType(MediaType.APPLICATION_OCTET_STREAM)
                    .body(bytes);
        } catch (Exception e) {
            return ResponseEntity.internalServerError().build();
        }
    }

    /**
     * 删除文件
     */
    @DeleteMapping("/delete")
    public Result deleteFile(@RequestParam String objectName, BucketEnum bucket) {
        minIOUpLoadService.delete(objectName, bucket);
        return Result.success();
    }

}
