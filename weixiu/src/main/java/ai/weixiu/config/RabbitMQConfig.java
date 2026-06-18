package ai.weixiu.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class RabbitMQConfig {

    public static final String EXCHANGE = "memory.exchange";
    public static final String DLX_EXCHANGE = "memory.dlx";

    public static final String REALTIME_QUEUE = "memory.realtime.queue";
    public static final String CONSOLIDATE_QUEUE = "memory.consolidate.queue";
    public static final String RESULT_QUEUE = "memory.result.queue";
    public static final String DLX_QUEUE = "memory.dlx.queue";

    // ===== 知识导入队列 =====
    public static final String KNOWLEDGE_EXCHANGE = "knowledge.exchange";
    public static final String KNOWLEDGE_IMPORT_QUEUE = "knowledge.import.queue";
    public static final String KNOWLEDGE_IMPORT_KEY = "knowledge.import";
    public static final String KNOWLEDGE_RESULT_QUEUE = "knowledge.result.queue";
    public static final String KNOWLEDGE_RESULT_KEY = "knowledge.result";

    // ===== 检修任务队列 =====
    public static final String TASK_EXCHANGE = "task.exchange";
    public static final String TASK_GENERATE_QUEUE = "task.generate.queue";
    public static final String TASK_GENERATE_KEY = "task.generate";
    public static final String TASK_GENERATE_RESULT_QUEUE = "task.generate.result.queue";
    public static final String TASK_GENERATE_RESULT_KEY = "task.generate.result";

    // ===== 步骤AI验证队列 =====
    public static final String TASK_STEP_VERIFY_QUEUE = "task.step.verify.queue";
    public static final String TASK_STEP_VERIFY_KEY = "task.step.verify";
    public static final String TASK_STEP_VERIFY_RESULT_QUEUE = "task.step.verify.result.queue";
    public static final String TASK_STEP_VERIFY_RESULT_KEY = "task.step.verify.result";

    // ===== 画像出题队列 =====
    public static final String QUIZ_GENERATE_QUEUE = "quiz.generate.queue";
    public static final String QUIZ_GENERATE_KEY = "quiz.generate";
    public static final String QUIZ_GENERATE_RESULT_QUEUE = "quiz.generate.result.queue";
    public static final String QUIZ_GENERATE_RESULT_KEY = "quiz.generate.result";

    // ===== 记忆反思队列 =====
    public static final String REFLECTION_QUEUE = "memory.reflection.queue";
    public static final String REFLECTION_KEY = "memory.reflection";
    public static final String REFLECTION_RESULT_QUEUE = "memory.reflection.result.queue";
    public static final String REFLECTION_RESULT_KEY = "memory.reflection.result";

    public static final String REALTIME_KEY = "memory.realtime";
    public static final String CONSOLIDATE_KEY = "memory.consolidate";
    public static final String RESULT_KEY = "memory.result";

    // ===== Dead Letter Exchange =====

    @Bean
    public FanoutExchange dlxExchange() {
        return new FanoutExchange(DLX_EXCHANGE, true, false);
    }

    @Bean
    public Queue dlxQueue() {
        return QueueBuilder.durable(DLX_QUEUE).build();
    }

    @Bean
    public Binding dlxBinding() {
        return BindingBuilder.bind(dlxQueue()).to(dlxExchange());
    }

    // ===== Main Exchange =====

    @Bean
    public TopicExchange memoryExchange() {
        return new TopicExchange(EXCHANGE, true, false);
    }

    // ===== Realtime Queue (TTL 5min) =====

    @Bean
    public Queue realtimeQueue() {
        return QueueBuilder.durable(REALTIME_QUEUE)
                .withArgument("x-message-ttl", 300_000)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
                .build();
    }

    @Bean
    public Binding realtimeBinding() {
        return BindingBuilder.bind(realtimeQueue()).to(memoryExchange()).with(REALTIME_KEY);
    }

    // ===== Consolidate Queue (TTL 10min) =====

    @Bean
    public Queue consolidateQueue() {
        return QueueBuilder.durable(CONSOLIDATE_QUEUE)
                .withArgument("x-message-ttl", 600_000)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
                .build();
    }

    @Bean
    public Binding consolidateBinding() {
        return BindingBuilder.bind(consolidateQueue()).to(memoryExchange()).with(CONSOLIDATE_KEY);
    }

    // ===== Result Queue (Python → Java) =====

    @Bean
    public Queue resultQueue() {
        return QueueBuilder.durable(RESULT_QUEUE).build();
    }

    @Bean
    public Binding resultBinding() {
        return BindingBuilder.bind(resultQueue()).to(memoryExchange()).with(RESULT_KEY);
    }

    // ===== Reflection Queue (TTL 10min) =====

    @Bean
    public Queue reflectionQueue() {
        return QueueBuilder.durable(REFLECTION_QUEUE)
                .withArgument("x-message-ttl", 600_000)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
                .build();
    }

    @Bean
    public Binding reflectionBinding() {
        return BindingBuilder.bind(reflectionQueue()).to(memoryExchange()).with(REFLECTION_KEY);
    }

    @Bean
    public Queue reflectionResultQueue() {
        return QueueBuilder.durable(REFLECTION_RESULT_QUEUE).build();
    }

    @Bean
    public Binding reflectionResultBinding() {
        return BindingBuilder.bind(reflectionResultQueue()).to(memoryExchange()).with(REFLECTION_RESULT_KEY);
    }

    // ===== Knowledge Import Exchange & Queues =====

    @Bean
    public TopicExchange knowledgeExchange() {
        return new TopicExchange(KNOWLEDGE_EXCHANGE, true, false);
    }

    /** 知识导入任务队列（TTL 30min，PDF 解析+向量化耗时较长） */
    @Bean
    public Queue knowledgeImportQueue() {
        return QueueBuilder.durable(KNOWLEDGE_IMPORT_QUEUE)
                .withArgument("x-message-ttl", 1_800_000)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
                .build();
    }

    @Bean
    public Binding knowledgeImportBinding() {
        return BindingBuilder.bind(knowledgeImportQueue()).to(knowledgeExchange()).with(KNOWLEDGE_IMPORT_KEY);
    }

    /** 知识导入结果队列（Python → Java） */
    @Bean
    public Queue knowledgeResultQueue() {
        return QueueBuilder.durable(KNOWLEDGE_RESULT_QUEUE).build();
    }

    @Bean
    public Binding knowledgeResultBinding() {
        return BindingBuilder.bind(knowledgeResultQueue()).to(knowledgeExchange()).with(KNOWLEDGE_RESULT_KEY);
    }

    // ===== Task Generate Exchange & Queues =====

    @Bean
    public TopicExchange taskExchange() {
        return new TopicExchange(TASK_EXCHANGE, true, false);
    }

    /** 检修步骤生成队列（TTL 5min，LLM推理耗时） */
    @Bean
    public Queue taskGenerateQueue() {
        return QueueBuilder.durable(TASK_GENERATE_QUEUE)
                .withArgument("x-message-ttl", 300_000)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
                .build();
    }

    @Bean
    public Binding taskGenerateBinding() {
        return BindingBuilder.bind(taskGenerateQueue()).to(taskExchange()).with(TASK_GENERATE_KEY);
    }

    /** 检修步骤生成结果队列（Python → Java） */
    @Bean
    public Queue taskGenerateResultQueue() {
        return QueueBuilder.durable(TASK_GENERATE_RESULT_QUEUE).build();
    }

    @Bean
    public Binding taskGenerateResultBinding() {
        return BindingBuilder.bind(taskGenerateResultQueue()).to(taskExchange()).with(TASK_GENERATE_RESULT_KEY);
    }

    // ===== Step Verify Exchange & Queues =====

    /** 步骤AI验证队列（TTL 5min，多模态LLM验证耗时） */
    @Bean
    public Queue stepVerifyQueue() {
        return QueueBuilder.durable(TASK_STEP_VERIFY_QUEUE)
                .withArgument("x-message-ttl", 300_000)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
                .build();
    }

    @Bean
    public Binding stepVerifyBinding() {
        return BindingBuilder.bind(stepVerifyQueue()).to(taskExchange()).with(TASK_STEP_VERIFY_KEY);
    }

    /** 步骤AI验证结果队列（Python → Java） */
    @Bean
    public Queue stepVerifyResultQueue() {
        return QueueBuilder.durable(TASK_STEP_VERIFY_RESULT_QUEUE).build();
    }

    @Bean
    public Binding stepVerifyResultBinding() {
        return BindingBuilder.bind(stepVerifyResultQueue()).to(taskExchange()).with(TASK_STEP_VERIFY_RESULT_KEY);
    }

    // ===== 画像出题队列（复用 task.exchange） =====

    /** 出题生成队列（TTL 5min，LLM 检索+生成耗时） */
    @Bean
    public Queue quizGenerateQueue() {
        return QueueBuilder.durable(QUIZ_GENERATE_QUEUE)
                .withArgument("x-message-ttl", 300_000)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
                .build();
    }

    @Bean
    public Binding quizGenerateBinding() {
        return BindingBuilder.bind(quizGenerateQueue()).to(taskExchange()).with(QUIZ_GENERATE_KEY);
    }

    /** 出题生成结果队列（Python → Java） */
    @Bean
    public Queue quizGenerateResultQueue() {
        return QueueBuilder.durable(QUIZ_GENERATE_RESULT_QUEUE).build();
    }

    @Bean
    public Binding quizGenerateResultBinding() {
        return BindingBuilder.bind(quizGenerateResultQueue()).to(taskExchange()).with(QUIZ_GENERATE_RESULT_KEY);
    }

    // ===== Serialization =====

    @Bean
    public MessageConverter jsonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public RabbitTemplate rabbitTemplate(ConnectionFactory connectionFactory, MessageConverter jsonMessageConverter) {
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        template.setMessageConverter(jsonMessageConverter);
        template.setConfirmCallback((data, ack, cause) -> {
            if (!ack) {
                // publisher-confirm 失败时仅记录日志，降级由发送端处理
                org.slf4j.LoggerFactory.getLogger(RabbitMQConfig.class)
                        .warn("MQ 消息发送确认失败: {}", cause);
            }
        });
        return template;
    }
}
