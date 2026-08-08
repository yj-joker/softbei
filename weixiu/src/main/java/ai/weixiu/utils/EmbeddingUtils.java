package ai.weixiu.utils;

import ai.weixiu.exception.EmbeddingException;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientRequestException;
import reactor.netty.http.client.HttpClient;
import io.netty.resolver.DefaultAddressResolverGroup;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Component
public class EmbeddingUtils {
    /** 与 Neo4j 文本/多模态向量索引统一为 1024 维。 */
    static final int TEXT_EMBEDDING_DIMENSIONS = 1024;

    private final ObjectMapper objectMapper;
    private final WebClient webClient;
    @Value("${apikey}")
    private String apiKey;
    public EmbeddingUtils(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
        HttpClient httpClient = HttpClient.create()
                .resolver(DefaultAddressResolverGroup.INSTANCE);
        this.webClient = WebClient.builder()
                .baseUrl("https://dashscope.aliyuncs.com/compatible-mode/v1")
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .build();
    }

    public List<Double> getEmbedding(String text) {
        String response;
        try {
            response = webClient.post()
                    .uri("/embeddings")
                    .header("Authorization", "Bearer " + apiKey)
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(objectMapper.writeValueAsString(Map.of(
                            "model", "text-embedding-v4",
                            "input", text,
                            "dimensions", TEXT_EMBEDDING_DIMENSIONS,
                            "encoding_format", "float"
                    )))
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
        } catch (JsonProcessingException e) {
            throw new EmbeddingException("向量化失败");
        } catch (WebClientRequestException e) {
            throw new EmbeddingException("remote embedding service unavailable", e);
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
        if (embedding.size() != TEXT_EMBEDDING_DIMENSIONS) {
            throw new EmbeddingException(
                    "文本向量维度异常，期望" + TEXT_EMBEDDING_DIMENSIONS + "，实际" + embedding.size()
            );
        }
        return embedding;
    }
}
