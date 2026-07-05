package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("domain_rule")
public class DomainRule implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    @TableField("rule_code")
    private String ruleCode;

    @TableField("title")
    private String title;

    @TableField("device_type")
    private String deviceType;

    @TableField("symptom_keys_json")
    private String symptomKeysJson;

    @TableField("condition_text")
    private String conditionText;

    @TableField("conclusion")
    private String conclusion;

    @TableField("question")
    private String question;

    @TableField("options_json")
    private String optionsJson;

    @TableField("evidence_refs_json")
    private String evidenceRefsJson;

    @TableField("status")
    private String status;

    @TableField("review_comment")
    private String reviewComment;

    @TableField("created_by_id")
    private Long createdById;

    @TableField("reviewed_by_id")
    private Long reviewedById;

    @TableField("created_at")
    private LocalDateTime createdAt;

    @TableField("updated_at")
    private LocalDateTime updatedAt;

    @TableField("reviewed_at")
    private LocalDateTime reviewedAt;

    @TableField("python_doc_id")
    private String pythonDocId;

    @TableField("sync_status")
    private String syncStatus;

    @TableField("sync_error")
    private String syncError;
}
