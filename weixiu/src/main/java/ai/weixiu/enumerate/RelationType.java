package ai.weixiu.enumerate;

import lombok.Getter;

@Getter
public enum RelationType {
    DEVICE_OWNS_COMPONENT, //设备拥有部件
    DEVICE_HAS_FAULT, //设备会发生故障
    COMPONENT_CAUSES_FAULT, //部件会导致故障
    FAULT_HAS_SOLUTION, //故障的解决方案
    CASE_RECORD_RECORDED_FAULT //案例记录了故障
}
