package ai.weixiu.pojo.query;

import lombok.Data;

import java.util.ArrayList;
import java.util.List;

/** Python 语义路由传入的结构化图谱查询契约。 */
@Data
public class GraphQueryContract {
    private String rawQuery = "";
    private String deviceIdentity = "";
    private String component = "";
    private String partSpec = "";
    private List<String> symptoms = new ArrayList<>();
    private List<String> operatingConditions = new ArrayList<>();
    private String taskAction = "";
    private String procedureAction = "";
    private String assemblyContext = "";
    private String orientation = "";
    private List<String> requestedFields = new ArrayList<>();
}
