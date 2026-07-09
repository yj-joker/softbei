package ai.weixiu.service.impl;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.UserMapper;
import ai.weixiu.pojo.vo.AdminOverviewVO;
import ai.weixiu.pojo.vo.UserOverviewVO;
import ai.weixiu.repository.CaseRecordRepository;
import ai.weixiu.repository.DeviceRepository;
import ai.weixiu.service.StatService;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 首页概览统计实现。所有指标均为对现有数据源的实时 count：
 *   - 任务：MyBatis-Plus selectCount（MySQL）
 *   - 设备：DeviceRepository（Neo4j）
 *   - 案例：CaseRecordRepository（Neo4j）
 *   - 用户：UserMapper（MySQL）
 * 不含任何写死/模拟数据。
 */
@Slf4j
@Service
@AllArgsConstructor
public class StatServiceImpl implements StatService {

    /** 检修任务的全部状态，顺序即前端流转展示顺序 */
    private static final String[] TASK_STATUSES =
            {"CREATED", "GENERATING", "GENERATED", "EXECUTING", "CLOSED"};

    private final MaintenanceTaskMapper taskMapper;
    private final UserMapper userMapper;
    private final DeviceRepository deviceRepository;
    private final CaseRecordRepository caseRecordRepository;

    @Override
    public UserOverviewVO getUserOverview() {
        Long userId = BaseContext.getCurrentId();

        UserOverviewVO vo = new UserOverviewVO();
        vo.setDeviceTotal(safeDeviceTotal());

        // 我的任务：按状态分布 + 汇总
        Map<String, Long> flow = new LinkedHashMap<>();
        long myTotal = 0L;
        long myClosed = 0L;
        for (String status : TASK_STATUSES) {
            long c = taskMapper.selectCount(new LambdaQueryWrapper<MaintenanceTask>()
                    .eq(userId != null, MaintenanceTask::getReporterId, userId)
                    .eq(MaintenanceTask::getStatus, status));
            flow.put(status, c);
            myTotal += c;
            if ("CLOSED".equals(status)) {
                myClosed = c;
            }
        }
        long myOpen = myTotal - myClosed;

        vo.setTaskFlow(flow);
        vo.setMyTaskTotal(myTotal);
        vo.setMyClosedTasks(myClosed);
        vo.setMyOpenTasks(myOpen);
        vo.setCompletionRate(rate(myClosed, myTotal));
        return vo;
    }

    @Override
    public AdminOverviewVO getAdminOverview() {
        AdminOverviewVO vo = new AdminOverviewVO();
        vo.setUserTotal(userMapper.selectCount(null));
        vo.setDeviceTotal(safeDeviceTotal());
        vo.setPendingCaseTotal(caseRecordRepository.countByStatus("pending"));

        Map<String, Long> dist = new LinkedHashMap<>();
        long taskTotal = 0L;
        for (String status : TASK_STATUSES) {
            long c = taskMapper.selectCount(new LambdaQueryWrapper<MaintenanceTask>()
                    .eq(MaintenanceTask::getStatus, status));
            dist.put(status, c);
            taskTotal += c;
        }
        vo.setTaskStatusDist(dist);
        vo.setTaskTotal(taskTotal);
        return vo;
    }

    /** 设备总数：DeviceRepository 传 null 关键字即全量 count；空结果兜底为 0，不抛出。 */
    private long safeDeviceTotal() {
        Long total = deviceRepository.getDeviceTotal(null);
        return total == null ? 0L : total;
    }

    /** 完成率百分比，保留一位小数；分母为 0 时返回 0。 */
    private double rate(long part, long total) {
        if (total <= 0) return 0d;
        return BigDecimal.valueOf(part * 100.0 / total)
                .setScale(1, RoundingMode.HALF_UP)
                .doubleValue();
    }
}
