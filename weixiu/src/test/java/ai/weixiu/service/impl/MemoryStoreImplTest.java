package ai.weixiu.service.impl;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.service.MemoryFactService;
import com.baomidou.mybatisplus.core.conditions.Wrapper;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 漏洞#3-B1 维度预筛注入的离线单测：直接验证 {@link MemoryStoreImpl#loadIndex} 的相关性重排与截断逻辑。
 * mock 掉 DB 层（{@link MemoryFactService}），不依赖运行中的服务/数据库。
 */
class MemoryStoreImplTest {

    private MemoryFact fact(String name, String deviceType, Long equipmentId, Long siteId, Long taskId, int importance) {
        MemoryFact f = new MemoryFact();
        f.setName(name);
        f.setType("project");
        f.setDescription("desc-" + name);
        f.setDeviceType(deviceType);
        f.setEquipmentId(equipmentId);
        f.setSiteId(siteId);
        f.setTaskId(taskId);
        f.setImportance(importance);
        return f;
    }

    /** 从 loadIndex 返回的目录文本里按顺序抽出 name（每行格式 "- [name] (type) — desc"）。 */
    private List<String> namesInOrder(String index) {
        List<String> names = new ArrayList<>();
        for (String raw : index.split("\n")) {
            String line = raw.trim();
            if (line.startsWith("- [")) {
                names.add(line.substring(3, line.indexOf(']')));
            }
        }
        return names;
    }

    private MemoryStoreImpl storeReturning(List<MemoryFact> facts) {
        MemoryFactService svc = Mockito.mock(MemoryFactService.class);
        when(svc.list(any(Wrapper.class))).thenReturn(facts);
        return new MemoryStoreImpl(svc);
    }

    @Test
    void dimensionPrefilter_prioritizesRelevant_truncatesOtherContext() {
        List<MemoryFact> facts = new ArrayList<>();
        // 2 条命中设备类型（rank 2）
        facts.add(fact("pump-A", "液压泵", null, null, null, 9));
        facts.add(fact("pump-B", "液压泵", null, null, null, 8));
        // 3 条通用、无维度绑定（rank 1）；其中一条 deviceType 为空串以测试空白判定
        facts.add(fact("gen-1", null, null, null, null, 7));
        facts.add(fact("gen-2", null, null, null, null, 6));
        facts.add(fact("gen-3", "", null, null, null, 5));
        // 35 条绑定到其它上下文（电动机，rank 0）—— 超过 OTHER_CONTEXT_LIMIT=30 以触发截断
        for (int i = 0; i < 35; i++) {
            facts.add(fact("motor-" + i, "电动机", null, null, null, 4));
        }
        // 入参已按重要度倒序（模拟 DB 返回）

        String index = storeReturning(facts).loadIndex(1L, "液压泵", null, null, null);
        List<String> names = namesInOrder(index);

        assertEquals(35, names.size(), "总数应为 2(相关)+3(通用)+30(其它截断) = 35");
        assertEquals(List.of("pump-A", "pump-B"), names.subList(0, 2), "命中设备类型(rank2)应排最前");
        assertTrue(names.subList(2, 5).containsAll(List.of("gen-1", "gen-2", "gen-3")), "通用(rank1)紧随其后");
        long motors = names.stream().filter(n -> n.startsWith("motor-")).count();
        assertEquals(30, motors, "其它上下文应被截断到 30 条");
        assertTrue(names.containsAll(List.of("pump-A", "pump-B", "gen-1", "gen-2", "gen-3")),
                "相关与通用全部保留，不丢失");
    }

    @Test
    void specificEquipmentMatch_outranksDeviceTypeMatch() {
        List<MemoryFact> facts = new ArrayList<>();
        facts.add(fact("by-device", "液压泵", null, null, null, 10));     // rank 2
        facts.add(fact("by-equipment", "液压泵", 100L, null, null, 1));   // rank 3（命中具体设备）
        String index = storeReturning(facts).loadIndex(1L, "液压泵", "100", null, null);
        List<String> names = namesInOrder(index);
        assertEquals("by-equipment", names.get(0),
                "命中具体设备(rank3)应高于仅命中设备类型(rank2)，即便重要度更低");
    }

    @Test
    void noContext_returnsAllInImportanceOrder_noTruncation() {
        List<MemoryFact> facts = new ArrayList<>();
        facts.add(fact("a", "液压泵", null, null, null, 9));
        for (int i = 0; i < 35; i++) {
            facts.add(fact("m-" + i, "电动机", null, null, null, 4));
        }
        String index = storeReturning(facts).loadIndex(1L, null, null, null, null); // 维度全空 → 退化为旧行为
        List<String> names = namesInOrder(index);
        assertEquals(36, names.size(), "无上下文维度时应保持全量，不做截断（与旧行为一致）");
        assertEquals("a", names.get(0), "退化时按入参(重要度)顺序");
    }
}
