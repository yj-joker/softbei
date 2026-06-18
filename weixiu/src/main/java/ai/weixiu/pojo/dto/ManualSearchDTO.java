package ai.weixiu.pojo.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Max;
import lombok.Data;

import java.util.List;

/**
 * 维修手册章节级搜索请求 DTO。
 *
 * <p>用户输入关键词（文字或图片 URL），从向量库检索最相关的文本块/图片/表格，
 * 返回结果中包含章节归属和页码定位。</p>
 */
@Data
public class ManualSearchDTO {

    /** 搜索关键词（必填） */
    @NotBlank(message = "搜索关键词不能为空")
    private String query;

    /** 返回数量，默认 10，最大 50 */
    private Integer topK = 10;

    /** 按手册 ID 过滤（可选，只搜某本手册） */
    private Long manualId;

    /** 按内容类型过滤：text / image / table（可选） */
    private String chunkType;

    /** 搜索图片 URL 列表（可选，图片搜索） */
    private List<String> images;

    /** 设备类型过滤（可选） */
    private String deviceType;
}
