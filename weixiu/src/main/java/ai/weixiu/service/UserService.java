package ai.weixiu.service;

import ai.weixiu.pojo.dto.UserDTO;
import ai.weixiu.pojo.dto.UserLoginDTO;
import ai.weixiu.entity.User;
import ai.weixiu.pojo.query.UserQuery;
import ai.weixiu.pojo.vo.BatchRegisterResultVO;
import ai.weixiu.pojo.vo.UserVO;
import com.baomidou.mybatisplus.extension.service.IService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * <p>
 * 用户表 服务类
 * </p>
 *
 * @author author
 * @since 2026-04-08
 */
public interface UserService extends IService<User> {

    BatchRegisterResultVO batchRegister(MultipartFile file);

    UserVO login(UserLoginDTO userLoginDTO, HttpServletRequest httpServletRequest);

    List<UserVO> getUserList(UserQuery userQuery);

    UserVO getUserById(Integer id);

    void updateUser(UserDTO userDTO);

    void sendEmail(String email, Integer mode);

    void verifyEmail(String code, Integer mode,String emailOrPassword);

    void removeUserByIds(List<Integer> ids);
}
