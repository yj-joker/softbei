package ai.weixiu;

import ai.weixiu.config.MinioProperties;
import ai.weixiu.config.DotEnvLoader;
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
        SpringApplication application = new SpringApplication(WeixiuApplication.class);
        DotEnvLoader.loadDefault().ifPresent(dotEnv -> {
            application.setDefaultProperties(dotEnv.properties());
            System.out.printf(
                    "Loaded %d properties from %s%n",
                    dotEnv.properties().size(),
                    dotEnv.path()
            );
        });
        application.run(args);
    }

}
