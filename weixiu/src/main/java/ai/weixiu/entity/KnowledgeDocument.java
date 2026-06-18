package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableField;
import java.time.LocalDateTime;
import java.io.Serializable;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("knowledge_document")
public class KnowledgeDocument implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    @TableField("manual_id")
    private Long manualId;

    /** 传给 Python 向量库的唯一标识，格式: "kdoc_{id}" */
    @TableField("document_id")
    private String documentId;

    @TableField("version")
    private Integer version;

    @TableField("file_name")
    private String fileName;

    @TableField("file_type")
    private String fileType;

    @TableField("file_size")
    private Long fileSize;

    @TableField("minio_object_name")
    private String minioObjectName;

    /** pending / parsing / indexing / ready / failed */
    @TableField("status")
    private String status;

    @TableField("error_message")
    private String errorMessage;

    @TableField("text_count")
    private Integer textCount;

    @TableField("image_count")
    private Integer imageCount;

    @TableField("table_count")
    private Integer tableCount;

    @TableField("created_by_id")
    private Long createdById;

    @TableField("created_at")
    private LocalDateTime createdAt;

    @TableField("updated_at")
    private LocalDateTime updatedAt;
}
