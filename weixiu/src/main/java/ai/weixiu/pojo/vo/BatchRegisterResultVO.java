package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.ArrayList;
import java.util.List;

/**
 * 批量注册用户回执：总数 / 成功 / 失败，以及失败明细（行号 + 账号 + 原因）。
 */
@Data
public class BatchRegisterResultVO {

    /** Excel 数据总行数 */
    private int total;
    /** 成功导入数 */
    private int success;
    /** 失败/跳过数 */
    private int failed;
    /** 失败明细 */
    private List<FailRow> failures = new ArrayList<>();

    @Data
    public static class FailRow {
        /** Excel 行号（含表头，从 2 起；0 表示按账号定位、行号不可知） */
        private int row;
        private String username;
        private String reason;

        public FailRow(int row, String username, String reason) {
            this.row = row;
            this.username = username;
            this.reason = reason;
        }
    }

    public void addFailure(int row, String username, String reason) {
        this.failures.add(new FailRow(row, username, reason));
        this.failed++;
    }
}
