package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.List;

@Data
public class GraphRagResultVO {
    private String question;
    private String deviceKeyword;
    private List<FaultVO> matchedFaults;
    private List<DiagnosisPathVO> diagnosisPaths;
    private String context;
}
