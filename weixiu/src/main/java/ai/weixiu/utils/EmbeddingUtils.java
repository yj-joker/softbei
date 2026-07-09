package ai.weixiu.utils;

import ai.weixiu.exception.EmbeddingException;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Component
public class EmbeddingUtils {
    private final ObjectMapper objectMapper;
    @Value("${apikey}")
    private String apiKey;
    public EmbeddingUtils(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public List<Double> getEmbedding(String text) {
        WebClient webClient = WebClient.create("https://dashscope.aliyuncs.com/compatible-mode/v1");
        String response;
        try {
            response = webClient.post()
                    .uri("/embeddings")
                    .header("Authorization", "Bearer " + apiKey)
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(objectMapper.writeValueAsString(Map.of(
                            "model", "text-embedding-v4",
                            "input", text,
                            "dimensions", 1024,
                            "encoding_format", "float"
                    )))
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
        } catch (JsonProcessingException e) {
            throw new EmbeddingException("向量化失败");
        }
        JsonNode root;
        try {
            root = objectMapper.readTree(response);
        } catch (JsonProcessingException e) {
            throw new EmbeddingException("解析向量化结果失败");
        }
        JsonNode dataArray = root.get("data");
        if (dataArray == null || !dataArray.isArray() || dataArray.isEmpty()) {
            throw new EmbeddingException("向量化返回数据格式错误");
        }
        JsonNode embeddingArray = dataArray.get(0).get("embedding");
        if (embeddingArray == null || !embeddingArray.isArray()) {
            throw new EmbeddingException("向量化结果格式错误");
        }

        List<Double> embedding = new ArrayList<>();
        for (JsonNode node : embeddingArray) {
            embedding.add(node.asDouble());
        }
        if (embedding.size() != 1024) {
            throw new EmbeddingException("文本向量维度异常，期望1024实际" + embedding.size());
        }
        return embedding;
    }
}
