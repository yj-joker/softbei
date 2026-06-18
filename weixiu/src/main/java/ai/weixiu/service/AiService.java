package ai.weixiu.service;

import ai.weixiu.entity.AiChatRequest;
import org.springframework.web.multipart.MultipartFile;
import reactor.core.publisher.Flux;

public interface AiService {
    Flux<String> chat(AiChatRequest request);

    String getStringByVoiceViaLLM(MultipartFile file);
}
