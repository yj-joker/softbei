package ai.weixiu.entity;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
public class AiChatRequest {
    @JsonProperty("session_id")
    private String sessionId;

    /**
     * 当前用户消息（纯文本）
     */
    @JsonProperty("message")
    private String userMessage;

    /**
     * 多轮对话历史，格式：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
     * Python端据此构建OpenAI多轮消息，让LLM正确区分用户和助手发言
     */
    @JsonProperty("conversation_history")
    private List<Map<String, String>> conversationHistory;

    /**
     * 结构化上下文信息（摘要、事实、偏好、待办）
     * Python端将其注入system prompt或作为辅助上下文
     */
    @JsonProperty("context")
    private Map<String, Object> context;

    /**
     * 用户上传的图片 URL 列表（MinIO 地址）
     * Python 端 FixAgent 会将图片传给图谱查询工具做多模态检索
     */
    @JsonProperty("images")
    private List<String> images;

    /**
     * 检索范围限定的设备型号（会话绑定）。
     *
     * <p>取值来源：由<b>前端</b>按当前会话绑定的设备/工单下发（AiSession 当前不持有设备绑定，
     * 故 Java 侧不补值，仅原样透传）。随请求体序列化转发给 Python。</p>
     *
     * <p>Python 端据此把知识库检索<b>强制锁定</b>在该设备名下的手册，LLM 无法放宽、无法跨设备；
     * 缺省（null）时 Python 退回全库检索，不报错（渐进生效）。</p>
     *
     * <p><b>字典对齐</b>：本字段取值必须与导入手册时写入的 device_type 元数据用<b>同一套字典</b>，
     * 否则过滤匹配不上会全部过滤、返回空。</p>
     */
    @JsonProperty("device_type")
    private String deviceType;

    /**
     * 检索范围限定的单本手册ID（可选，比 deviceType 更严）。缺省时不限定到单本，由前端按需下发。
     */
    @JsonProperty("document_id")
    private String documentId;
}
