package ai.weixiu;

import ai.weixiu.config.MinioProperties;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
@MapperScan("ai.weixiu.mapper")
@EnableConfigurationProperties({MinioProperties.class})
public class WeixiuApplication {

    public static void main(String[] args) {
        SpringApplication.run(WeixiuApplication.class, args);
    }

}
