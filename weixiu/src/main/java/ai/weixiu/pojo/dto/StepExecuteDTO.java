package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

/** 执行某一步骤的请求体 */
@Data
public class StepExecuteDTO {

    /** 工人上传的照片URL列表 */
    private List<String> images;

    /** 工人填写的备注 */
    private String note;

    /** 合规检查点确认（如果该步骤是检查点，必须为true才能提交） */
    private Boolean checkpointConfirmed;
}
