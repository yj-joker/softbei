package ai.weixiu.config;

import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;
import org.redisson.config.SingleServerConfig;
import org.springframework.boot.autoconfigure.data.redis.RedisProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;

@Configuration
public class RedissonConfiguration {

    /**
     * Redisson 这里只用于分布式互斥锁，Spring Data Redis 仍沿用项目原有的
     * 连接工厂和 RedisTemplate 配置。
     *
     * <p>本地开发 Redis 可能没有开启 AUTH。只有配置了真实密码时才给
     * Redisson 设置密码，否则空字符串也可能触发 AUTH 并被 Redis 拒绝。</p>
     */
    @Bean(destroyMethod = "shutdown")
    public RedissonClient redissonClient(RedisProperties redisProperties) {
        Config config = new Config();
        SingleServerConfig serverConfig = config.useSingleServer()
                .setAddress("redis://" + redisProperties.getHost() + ":" + redisProperties.getPort())
                .setDatabase(redisProperties.getDatabase());
        if (StringUtils.hasText(redisProperties.getPassword())) {
            serverConfig.setPassword(redisProperties.getPassword());
        }
        return Redisson.create(config);
    }
}
