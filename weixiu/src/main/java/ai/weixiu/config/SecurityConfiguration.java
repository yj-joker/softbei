package ai.weixiu.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

@Configuration
public class SecurityConfiguration {

    @Bean
    public PasswordEncoder passwordEncoder() {
        // cost值范围4-31，值越大越慢越安全。默认10，这里改为4提升性能
        return new BCryptPasswordEncoder(4);
    }
}
