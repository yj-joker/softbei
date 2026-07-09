package ai.weixiu.service.impl;

import ai.weixiu.common.RedisKey;
import ai.weixiu.enumerate.EmailEnum;
import ai.weixiu.exception.*;
import ai.weixiu.pojo.dto.UserDTO;
import ai.weixiu.pojo.dto.UserLoginDTO;
import ai.weixiu.entity.User;
import ai.weixiu.mapper.UserMapper;
import ai.weixiu.pojo.query.UserQuery;
import ai.weixiu.pojo.vo.BatchRegisterResultVO;
import ai.weixiu.pojo.vo.UserVO;
import ai.weixiu.service.UserService;
import ai.weixiu.utils.BaseContext;
import ai.weixiu.utils.ExcelUtils;
import ai.weixiu.utils.IsNullUtils;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

/**
 * <p>
 * 用户表 服务实现类
 * </p>
 *
 * @author author
 * @since 2026-04-08
 */
@Service
@Slf4j
public class UserServiceImpl extends ServiceImpl<UserMapper, User> implements UserService {

    private final RedisTemplate redisTemplate;
    private final PasswordEncoder passwordEncoder;
    private final JavaMailSender javaMailSender;
    private String MyEmail;

    @Autowired
    public UserServiceImpl(RedisTemplate redisTemplate, PasswordEncoder passwordEncoder, JavaMailSender javaMailSender) {
        this.redisTemplate = redisTemplate;
        this.passwordEncoder = passwordEncoder;
        this.javaMailSender = javaMailSender;
    }

    @Value("${spring.mail.username}")
    public void setMyEmail(String MyEmail) {
        this.MyEmail = MyEmail;
    }

    /*
     * 批量添加 用户
     * */
    @Override
    @Transactional
    public BatchRegisterResultVO batchRegister(MultipartFile file) {
        // 1. 文件类型校验
        if (!ExcelUtils.isExcelFile(file)) {
            throw new FormatErrorException("必须上传excel文件");
        }
        List<User> rows = ExcelUtils.readExcel(file, User.class);
        log.info("共读取到 {} 条数据，开始处理", rows.size());

        BatchRegisterResultVO result = new BatchRegisterResultVO();
        result.setTotal(rows.size());
        if (rows.isEmpty()) {
            return result;
        }

        // 2. 行内校验 + 文件内去重（username=身份证号）
        List<User> candidates = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        int rowNo = 1; // 表头第 1 行，数据从第 2 行起
        for (User u : rows) {
            rowNo++;
            String username = u.getUsername() == null ? null : u.getUsername().trim();
            if (username == null || username.isBlank()) {
                result.addFailure(rowNo, username, "身份证号(登录账号)为空");
                continue;
            }
            if (u.getName() == null || u.getName().isBlank()) {
                result.addFailure(rowNo, username, "姓名为空");
                continue;
            }
            if (!seen.add(username)) {
                result.addFailure(rowNo, username, "文件内身份证号重复");
                continue;
            }
            u.setUsername(username);
            candidates.add(u);
        }

        // 3. 与数据库已有账号去重（一次性查询）
        if (!candidates.isEmpty()) {
            Set<String> names = candidates.stream().map(User::getUsername).collect(Collectors.toSet());
            Set<String> existing = this.list(new LambdaQueryWrapper<User>()
                            .select(User::getUsername).in(User::getUsername, names))
                    .stream().map(User::getUsername).collect(Collectors.toSet());
            if (!existing.isEmpty()) {
                candidates.removeIf(u -> {
                    if (existing.contains(u.getUsername())) {
                        result.addFailure(0, u.getUsername(), "账号已存在");
                        return true;
                    }
                    return false;
                });
            }
        }

        if (candidates.isEmpty()) {
            log.info("批量注册无有效数据：总{} 失败{}", result.getTotal(), result.getFailed());
            return result;
        }

        // 4. 服务端强制安全字段（防 Excel 越权）：
        //    - 密码统一默认 123456（每行独立加盐，并行加密提速）
        //    - type 强制 0(员工)，杜绝 Excel 把 用户类型 填成 1 批量造管理员
        //    - status 置 1(已激活)，避免 login 对 null status 拆箱 NPE
        LocalDateTime now = LocalDateTime.now();
        candidates.parallelStream().forEach(u -> {
            u.setId(null);
            u.setPassword(passwordEncoder.encode("123456"));
            u.setType(0);
            u.setStatus(1);
            u.setCreateTime(now);
            u.setUpdateTime(now);
        });

        // 5. 入库（方法 @Transactional，整批原子；候选已去重/校验，正常不会失败）
        this.saveBatch(candidates);
        result.setSuccess(candidates.size());
        log.info("批量注册完成：总{} 成功{} 失败{}", result.getTotal(), result.getSuccess(), result.getFailed());
        return result;
    }

    /*
     * 用户登录
     * */
    @Override
    public UserVO login(UserLoginDTO userLoginDTO, HttpServletRequest httpServletRequest) {
        // 查询用户
        LambdaQueryWrapper<User> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(User::getUsername, userLoginDTO.getUsername());
        User user = this.getOne(queryWrapper);
        // 用户不存在
        if (user == null) {
            log.info("用户不存在");
            throw new NameOrPasswordException("用户名或密码错误");
        } else {
            //进行bcrypt密码校验
            if (!passwordEncoder.matches(userLoginDTO.getPassword(), user.getPassword())) {
                log.info("密码错误");
                throw new NameOrPasswordException("用户名或密码错误");
            }
        }
//        if (Objects.equals(user.getStatus(), StatusEnum.DEACTIVATED.getCode())) {
//            throw new ArithmeticException("用户未激活,请先激活");
//        }
        // 登录成功,设置最后登录时间
        user.setLastLoginTime(LocalDateTime.now());
        //如果第一次登录设置用户状态为已经激活
        if (user.getStatus() == 0) {
            user.setStatus(1);
        }
        this.updateById(user);
        HttpSession httpSession = httpServletRequest.getSession();
        //设置用户id到redis当中,过期时间1天
        redisTemplate.opsForValue().set(RedisKey.USER_SESSION_ID + httpSession.getId(), user.getId(), 1, TimeUnit.DAYS);
        log.info("设置会话成功");
        //封装vo层数据
        UserVO userVO = new UserVO();
        BeanUtils.copyProperties(user, userVO);
        log.info("用户登录成功");
        return userVO;
    }

    /*
     * 用户分页查询
     * */
    @Override
    public List<UserVO> getUserList(UserQuery userQuery) {
        Page<User> page = new Page<>(userQuery.getPage(), userQuery.getSize());
        LambdaQueryWrapper<User> wrapper = new LambdaQueryWrapper<>();

        // 链式拼接查询条件，null 和 "" 自动跳过
        wrapper.like(StringUtils.hasText(userQuery.getName()), User::getName, userQuery.getName())
                .eq(StringUtils.hasText(userQuery.getNumber()), User::getNumber, userQuery.getNumber())
                .eq(userQuery.getGender() != null, User::getGender, userQuery.getGender())
                .like(StringUtils.hasText(userQuery.getPhone()), User::getPhone, userQuery.getPhone())
                .ge(userQuery.getHireDate() != null, User::getHireDate, userQuery.getHireDate());

        // 排序
        if (userQuery.getSortBy() != null) {
            wrapper.orderBy(true,
                    userQuery.getIsAsc() == 1,
                    User::getCreateTime); // 替换为实际的排序字段
        }

        Page<User> result = this.page(page, wrapper);
        return result.getRecords().stream().map(user -> {
            UserVO vo = new UserVO();
            BeanUtils.copyProperties(user, vo);
            return vo;
        }).toList();
    }

    /*
     * 根据用户id查询对应用户
     * */
    @Override
    public UserVO getUserById(Integer id) {
        UserVO userVO = new UserVO();
        User user = this.getById(id);
        if (IsNullUtils.isNull(user)) {
            throw new NotFoundException("用户不存在");
        }
        BeanUtils.copyProperties(user, userVO);
        log.info("查询用户成功");
        return userVO;
    }

    /*
     * 修改用户信息
     * */
    @Override
    public void updateUser(UserDTO userDTO) {
       User user=this.getById(userDTO.getId());
       if(user==null){
           throw new NotFoundException("用户不存在");
       }
       user.setPhone(userDTO.getPhone());
       if(userDTO.getName().isEmpty()){
           user.setName(userDTO.getName());
       }
       user.setEmail(userDTO.getEmail());
        this.updateById(user);
    }

    /*
     * 发送验证码
     * */
    @Override
    public void sendEmail(String email, Integer mode) {
        //先判断邮箱格式是否符合规则
        if (!email.matches("^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\\.[a-zA-Z0-9_-]+)+$")) {
            throw new EmailException("邮箱格式不正确");
        }
        //校验 mode 是否合法
        if (!Objects.equals(mode, EmailEnum.ACTIVATION_EMAIL.getCode())
                && !Objects.equals(mode, EmailEnum.RESET_PASSWORD_EMAIL.getCode())) {
            throw new EmailException("无效的操作类型");
        }
        //重置密码模式下，校验邮箱是否属于当前用户（防止给任意邮箱发验证码）
        if (Objects.equals(mode, EmailEnum.RESET_PASSWORD_EMAIL.getCode())) {
            User user = this.getById(BaseContext.getCurrentId());
            if (user == null || user.getEmail() == null || !user.getEmail().equals(email)) {
                throw new EmailException("该邮箱与当前账号不匹配");
            }
        }
        //判断用户是否反复发送（Boolean.TRUE.equals 防止 hasKey 返回 null 导致 NPE）
        if (Boolean.TRUE.equals(redisTemplate.hasKey(RedisKey.USER_EMAIL_CODE + BaseContext.getCurrentId()))) {
            throw new EmailException("请勿重复发送验证码");
        }
        SimpleMailMessage message = new SimpleMailMessage();
        //从配置文件中获取当前设置的邮箱
        message.setFrom(MyEmail);
        //设置接收者邮箱
        message.setTo(email);
        //根据不同mode设置不同邮件标题
        if (Objects.equals(mode, EmailEnum.ACTIVATION_EMAIL.getCode())) {
            message.setSubject("维修平台绑定邮箱");
        } else if (Objects.equals(mode, EmailEnum.RESET_PASSWORD_EMAIL.getCode())) {
            message.setSubject("维修平台重置密码");
        }
        //设置邮件内容
        String code = getCode();
        message.setText("此次验证码:" + code);
        //将验证码存入redis中，并设置过期时间
        redisTemplate.opsForValue().set(RedisKey.USER_EMAIL_CODE + BaseContext.getCurrentId(), code, 1, TimeUnit.MINUTES);
        javaMailSender.send(message);
    }

    /*
     * 验证验证码
     * */
    @Override
    public void verifyEmail(String code, Integer mode,String emailOrPassword) {
     //从redis当中取出对应验证码
     String redisCode = (String) redisTemplate.opsForValue().get(RedisKey.USER_EMAIL_CODE + BaseContext.getCurrentId());
     if (redisCode == null) {
         throw new EmailException("请先发送验证码");
     }
     //MessageDigest.isEqual() 时间恒定比较，防止验证码被计时攻击猜测
     if (!MessageDigest.isEqual(code.getBytes(), redisCode.getBytes())) {
         throw new EmailException("验证码错误,请重新输入");
     }
     User user = this.getById(BaseContext.getCurrentId());
     if (user == null) {
         throw new EmailException("用户不存在");
     }

     if(Objects.equals(mode, EmailEnum.ACTIVATION_EMAIL.getCode())){
         //激活邮箱
          user.setEmail(emailOrPassword);
          this.updateById(user);
         redisTemplate.delete(RedisKey.USER_EMAIL_CODE + BaseContext.getCurrentId());
         log.info("邮箱绑定成功");
     }
    else if(Objects.equals(mode, EmailEnum.RESET_PASSWORD_EMAIL.getCode())){
         //重置密码
         String encode = passwordEncoder.encode(emailOrPassword);
         user.setPassword(encode);
         this.updateById(user);
         redisTemplate.delete(RedisKey.USER_EMAIL_CODE + BaseContext.getCurrentId());
         log.info("密码重置成功");
     }
     else{
         throw new EmailException("无效的操作类型");
     }
    }

    private String getCode() {
        String chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
        java.security.SecureRandom random = new java.security.SecureRandom();
        StringBuilder code = new StringBuilder();
        for (int i = 0; i < 6; i++) {
            code.append(chars.charAt(random.nextInt(chars.length())));
        }
        return code.toString();
    }

    @Override
    public void removeUserByIds(List<Integer> ids) {
        List<User> users = this.listByIds(ids);
        users.forEach(user -> {
          if(user.getType()==1){
              throw new DeleteException("不能删除管理员");
          }
        });
        this.removeByIds(ids);
    }
}
