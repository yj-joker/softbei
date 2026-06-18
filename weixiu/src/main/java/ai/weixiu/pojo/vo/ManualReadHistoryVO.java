package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;

/**
 * 用户阅读历史列表项。
 */
@Data
public class ManualReadHistoryVO {
    /** 手册 ID */
    private Long manualId;

    /** 手册名称 */
    private String manualName;

    /** 手册封面 */
    private String manualImage;

    /** 手册简介 */
    private String manualDesc;

    /** 最近一次打开时间 */
    private LocalDateTime lastReadAt;
}
