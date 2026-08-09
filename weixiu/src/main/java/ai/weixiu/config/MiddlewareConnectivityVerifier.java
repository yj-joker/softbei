package ai.weixiu.config;

import com.rabbitmq.client.Channel;
import io.minio.BucketExistsArgs;
import io.minio.MinioClient;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.neo4j.driver.Driver;
import org.neo4j.driver.Session;
import org.neo4j.driver.SessionConfig;
import org.springframework.amqp.rabbit.connection.Connection;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.connection.RedisConnection;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.ResultSet;
import java.sql.Statement;

/**
 * Java 侧中间件连通性验证器。
 *
 * <p>默认关闭，仅当 {@code app.middleware-verification.enabled=true} 时执行。
 * 任一中间件验证失败都会阻止应用以“假启动”状态继续运行。</p>
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "app.middleware-verification", name = "enabled", havingValue = "true")
public class MiddlewareConnectivityVerifier implements ApplicationRunner {

    private final DataSource dataSource;
    private final StringRedisTemplate stringRedisTemplate;
    private final Driver neo4jDriver;
    private final ConnectionFactory rabbitConnectionFactory;
    private final MinioClient minioClient;
    private final MinioProperties minioProperties;

    @Value("${spring.data.neo4j.database:neo4j}")
    private String neo4jDatabase;

    @Override
    public void run(ApplicationArguments args) throws Exception {
        log.info("[中间件验证] 开始从 Java 端验证 MySQL、Redis、Neo4j、RabbitMQ、MinIO");
        verifyMySql();
        verifyRedis();
        verifyNeo4j();
        verifyRabbitMq();
        verifyMinio();
        log.info("[中间件验证] 全部通过：5/5 个中间件可由 Java 正常访问");
    }

    private void verifyMySql() throws Exception {
        try (java.sql.Connection connection = dataSource.getConnection();
             Statement statement = connection.createStatement();
             ResultSet resultSet = statement.executeQuery("SELECT 1")) {
            if (!resultSet.next() || resultSet.getInt(1) != 1) {
                throw new IllegalStateException("MySQL SELECT 1 未返回预期结果");
            }
        }
        log.info("[中间件验证] MySQL 连接成功（SELECT 1）");
    }

    private void verifyRedis() {
        if (stringRedisTemplate.getConnectionFactory() == null) {
            throw new IllegalStateException("RedisConnectionFactory 未初始化");
        }
        try (RedisConnection connection = stringRedisTemplate.getConnectionFactory().getConnection()) {
            String pong = connection.ping();
            if (!"PONG".equalsIgnoreCase(pong)) {
                throw new IllegalStateException("Redis PING 未返回 PONG");
            }
        }
        log.info("[中间件验证] Redis 连接成功（PING/PONG）");
    }

    private void verifyNeo4j() {
        SessionConfig sessionConfig = SessionConfig.builder().withDatabase(neo4jDatabase).build();
        try (Session session = neo4jDriver.session(sessionConfig)) {
            int value = session.run("RETURN 1 AS ok").single().get("ok").asInt();
            if (value != 1) {
                throw new IllegalStateException("Neo4j RETURN 1 未返回预期结果");
            }
        }
        log.info("[中间件验证] Neo4j 连接成功（RETURN 1）");
    }

    private void verifyRabbitMq() throws Exception {
        try (Connection connection = rabbitConnectionFactory.createConnection();
             Channel channel = connection.createChannel(false)) {
            // 主动按 Java/Python 共同约定的参数声明，能识别“队列存在但参数不一致”的 406 错误。
            for (String queue : resultQueues()) {
                channel.queueDeclare(queue, true, false, false, null);
            }
        }
        log.info("[中间件验证] RabbitMQ 连接和 {} 个结果队列参数验证成功", resultQueues().length);
    }

    private String[] resultQueues() {
        return new String[]{
                RabbitMQConfig.RESULT_QUEUE,
                RabbitMQConfig.KNOWLEDGE_RESULT_QUEUE,
                RabbitMQConfig.TASK_GENERATE_RESULT_QUEUE,
                RabbitMQConfig.TASK_EVIDENCE_EXTRACT_RESULT_QUEUE,
                RabbitMQConfig.TASK_STEP_VERIFY_RESULT_QUEUE,
                RabbitMQConfig.QUIZ_GENERATE_RESULT_QUEUE,
                RabbitMQConfig.REFLECTION_RESULT_QUEUE
        };
    }

    private void verifyMinio() throws Exception {
        String bucket = minioProperties.getBucket();
        boolean exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build());
        if (!exists) {
            throw new IllegalStateException("MinIO 默认存储桶不存在: " + bucket);
        }
        log.info("[中间件验证] MinIO 连接成功，默认存储桶存在: {}", bucket);
    }
}
