package ai.weixiu.utils;

import org.springframework.beans.BeanUtils;

/**
 * 实体 → VO 转换工具。
 * 仅复制「同名属性」，因此 Neo4j 实体上的 multimodalEmbedding（1024 维向量）、
 * 关系集合（ownedComponents 等）等不应返回前端的字段会被自动丢弃，
 * 既避免前端解析超长浮点数组出错，也大幅减小响应体积。
 */
public final class VoConverter {

    private VoConverter() {}

    public static <T> T convert(Object source, Class<T> target) {
        if (source == null) return null;
        try {
            T vo = target.getDeclaredConstructor().newInstance();
            BeanUtils.copyProperties(source, vo);
            return vo;
        } catch (ReflectiveOperationException e) {
            throw new RuntimeException("VO 转换失败: " + target.getSimpleName(), e);
        }
    }
}
