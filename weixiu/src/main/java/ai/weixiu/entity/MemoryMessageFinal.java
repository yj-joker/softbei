package ai.weixiu.entity;

import lombok.Data;

import java.util.List;

@Data
public class MemoryMessageFinal {
    private Long sessionId;
    private List<MemoryMessage> memoryMessages;
}
