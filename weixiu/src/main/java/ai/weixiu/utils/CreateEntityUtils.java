package ai.weixiu.utils;

import ai.weixiu.entity.Component;
import ai.weixiu.entity.Device;
import ai.weixiu.entity.Fault;
import ai.weixiu.entity.Solution;
import ai.weixiu.repository.DeviceRepository;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.*;

/**
 * 知识图谱测试数据生成工具
 * <p>
 * 生成 120 个实体（10 Device + 30 Component + 40 Fault + 40 Solution），
 * 并建立完整的关系链：Device -[OWNS]→ Component -[CAUSES]→ Fault -[HAS_SOLUTION]→ Solution
 * <p>
 * 向量通过 EmbeddingUtils（文本1536维）和 MultimodalEmbeddingUtils（多模态1024维）生成，
 * 22 张图片 URL 随机分配给不同实体，每个实体最多 1 张，不复用。
 */
@Service
@AllArgsConstructor
@Slf4j
public class CreateEntityUtils {

    private final DeviceRepository deviceRepository;
    private final EmbeddingUtils embeddingUtils;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    private final BuildStringUtils buildStringUtils;

    private static final List<String> IMAGE_URLS = List.of(
            "http://localhost:9000/weixiu-public-tupian/2ae95c9ff49a4807b43e4485e2b2b092.jpg",
            "http://localhost:9000/weixiu-public-tupian/6427b3c37b8b48878dd77929984178c9.webp",
            "http://localhost:9000/weixiu-public-tupian/a9c5d91d7b6942a0aa29cfd12c4b0aed.jpg",
            "http://localhost:9000/weixiu-public-tupian/c0b5972e56324fb8b89cb8af34d83cb2.jpg",
            "http://localhost:9000/weixiu-public-tupian/71ec79fe22864f0bb45ba6f28ecfef7e.jpg",
            "http://localhost:9000/weixiu-public-tupian/6bf0004dde9a40f9b8426d8ef01d1b35.webp",
            "http://localhost:9000/weixiu-public-tupian/2f0302746c4c4091a2e7c57dd305bcd8.webp",
            "http://localhost:9000/weixiu-public-tupian/20df6d1526fc4720b3dba7f62bd2b0c1.jpg",
            "http://localhost:9000/weixiu-public-tupian/437866a7cc094cdd85f3f05c813f4ae9.jpg",
            "http://localhost:9000/weixiu-public-tupian/ce50c4ee55bb4d72b3b604d85e599b28.jpg",
            "http://localhost:9000/weixiu-public-tupian/268d493a6dd94918ba9a62ea411fc0c4.jpg",
            "http://localhost:9000/weixiu-public-tupian/cec4a463c6114f40979fe2e485ba3d2a.jpg",
            "http://localhost:9000/weixiu-public-tupian/9b11f26eeac24d728c9f1bd1be5d53da.jpg",
            "http://localhost:9000/weixiu-public-tupian/21a3ddce1b30408284187759090f5381.jpg",
            "http://localhost:9000/weixiu-public-tupian/2cca02b8500c4dd3962ddef39ae4f832.jpg",
            "http://localhost:9000/weixiu-public-tupian/abb1866b33de47568744b576abfc9928.jpg",
            "http://localhost:9000/weixiu-public-tupian/61a2ffd926ef44a288a90ef08dc90021.jpg",
            "http://localhost:9000/weixiu-public-tupian/4f8483005e714c40912c4e7b7e814b93.jpg",
            "http://localhost:9000/weixiu-public-tupian/9366123447c540f69a49a9cdf6c2be8b.jpg",
            "http://localhost:9000/weixiu-public-tupian/becf717a6015453db8e247349ac5e770.png",
            "http://localhost:9000/weixiu-public-tupian/8e19b2b72fdf4d0d86239985d99e70b8.webp",
            "http://localhost:9000/weixiu-public-tupian/e6a366712cca4f47a33ea5718fb6d113.jpg"
    );

    /**
     * 生成 120 个测试实体并写入 Neo4j。
     * <p>
     * 分布：10 Device + 30 Component + 40 Fault + 40 Solution = 120
     * 关系：Device→OWNS→Component→CAUSES→Fault→HAS_SOLUTION→Solution
     */
    public void generateTestData() {
        log.info("===== 开始生成测试数据 =====");

        List<String> shuffledUrls = new ArrayList<>(IMAGE_URLS);
        Collections.shuffle(shuffledUrls, new Random(42));
        int urlIndex = 0;

        // ===== 1. 构建 Solution（40个）=====
        String[][] solutionData = {
                {"更换轴承", "拆卸旧轴承并安装新轴承，确保同心度达标", "轴承拉马、扭力扳手", "中等", "60"},
                {"清洗滤网", "使用超声波清洗机清洗堵塞的滤网", "超声波清洗机", "简单", "30"},
                {"更换密封圈", "拆除旧密封圈并安装新O型密封圈", "密封圈安装工具", "简单", "20"},
                {"校准传感器", "使用标准信号源对传感器进行零点和满量程校准", "信号发生器、万用表", "中等", "45"},
                {"更换电机", "断电后拆卸旧电机，安装新电机并校准同轴度", "电动扳手、千分表", "复杂", "120"},
                {"修复线路短路", "检测短路点并更换损坏的线缆", "万用表、热缩管", "中等", "40"},
                {"更换控制板", "断电后更换PLC控制板并重新编程", "螺丝刀、编程线缆", "复杂", "90"},
                {"润滑保养", "对运动部件进行润滑脂加注和清洁", "润滑枪、清洗剂", "简单", "25"},
                {"紧固螺栓", "检查并重新紧固松动的螺栓连接", "扭力扳手", "简单", "15"},
                {"更换皮带", "拆除旧传动皮带并安装新皮带，调整张紧力", "皮带张力计", "中等", "35"},
                {"修复泄漏", "定位泄漏点并进行焊接或更换管道", "焊枪、管道切割器", "复杂", "80"},
                {"更换阀门", "关闭管路后更换故障阀门", "管钳、密封胶带", "中等", "50"},
                {"清理积碳", "使用化学清洗剂清除燃烧室积碳", "清洗剂、刮刀", "中等", "60"},
                {"重新接线", "按接线图重新连接松脱的端子", "剥线钳、压线钳", "简单", "30"},
                {"更换保险丝", "检测烧毁保险丝并更换同规格新保险丝", "保险丝座工具", "简单", "10"},
                {"调整间隙", "使用塞尺测量并调整部件间隙至标准范围", "塞尺", "中等", "40"},
                {"更换滤芯", "拆卸旧滤芯并安装新滤芯", "滤芯扳手", "简单", "20"},
                {"修复裂纹", "对裂纹部位进行焊接修复并做无损检测", "焊机、探伤仪", "复杂", "100"},
                {"更换热电偶", "拆除旧热电偶并安装校准后的新热电偶", "扳手、温度校准器", "中等", "35"},
                {"软件升级", "升级控制系统固件至最新版本", "编程电缆、笔记本电脑", "中等", "45"},
                {"更换减速器", "拆卸旧减速器并安装新减速器，调整传动比", "起重设备、扭力扳手", "复杂", "150"},
                {"清洗冷却系统", "排放旧冷却液并清洗管路，加注新冷却液", "排液泵、冷却液", "中等", "60"},
                {"更换触摸屏", "拆除损坏的HMI触摸屏并安装新屏", "螺丝刀", "中等", "40"},
                {"调整液压压力", "使用压力表校准液压系统工作压力", "压力表、调压阀扳手", "中等", "30"},
                {"更换继电器", "更换失效的继电器并测试动作是否正常", "螺丝刀、万用表", "简单", "20"},
                {"修复变频器故障", "检测变频器参数并重新设定，更换损坏模块", "万用表、示波器", "复杂", "90"},
                {"更换油封", "拆卸轴端油封并安装新油封，防止漏油", "油封安装工具", "中等", "35"},
                {"重新校正编码器", "调整编码器安装位置并校准零位信号", "示波器、六角扳手", "中等", "50"},
                {"更换液压油", "排放旧液压油并加注新油，排除空气", "排油泵、液压油", "简单", "40"},
                {"更换链条", "拆除旧传动链条并安装新链条，调整松紧度", "链条拆装工具", "中等", "45"},
                {"修复电磁阀", "清洗或更换电磁阀阀芯，恢复动作", "清洗剂、万用表", "中等", "35"},
                {"更换制动片", "拆除磨损制动片并安装新制动片", "扳手", "简单", "25"},
                {"调整对中", "使用激光对中仪调整电机与泵的同轴度", "激光对中仪", "复杂", "60"},
                {"更换气缸密封件", "拆卸气缸并更换全部密封件", "密封件套装", "中等", "50"},
                {"清洗喷嘴", "使用超声波清洗堵塞的喷嘴", "超声波清洗机", "简单", "25"},
                {"更换散热风扇", "拆除故障风扇并安装新风扇", "螺丝刀", "简单", "15"},
                {"修复接地故障", "检测并修复接地线连接不良", "接地电阻测试仪", "中等", "40"},
                {"更换压力开关", "拆除旧压力开关并安装校准后的新开关", "扳手、压力源", "中等", "30"},
                {"修复振动异常", "动平衡校正或更换不平衡部件", "振动分析仪", "复杂", "80"},
                {"更换光栅尺", "拆除旧光栅尺并安装新光栅尺，校准精度", "千分表、螺丝刀", "复杂", "70"}
        };

        List<Solution> allSolutions = new ArrayList<>();
        for (int i = 0; i < solutionData.length; i++) {
            String[] d = solutionData[i];
            Solution solution = Solution.builder()
                    .code("S" + String.format("%03d", i + 1))
                    .title(d[0])
                    .description(d[1])
                    .toolsRequired(d[2])
                    .difficulty(d[3])
                    .estimatedTime(Integer.parseInt(d[4]))
                    .createdAt(LocalDateTime.now().minusDays(new Random(i).nextInt(365)))
                    .verified(i % 3 != 0)
                    .build();

            if (urlIndex < shuffledUrls.size() && i < 5) {
                solution.setImageUrls(List.of(shuffledUrls.get(urlIndex++)));
            }
            allSolutions.add(solution);
        }
        log.info("构建 {} 个 Solution", allSolutions.size());

        // ===== 2. 构建 Fault（40个）并关联 Solution =====
        String[][] faultData = {
                {"主轴异响", "主轴运转时发出异常噪音，可能是轴承磨损或润滑不足"},
                {"进给轴抖动", "X轴进给时出现抖动，影响加工精度"},
                {"液压系统压力不足", "液压系统压力低于设定值，无法正常工作"},
                {"冷却液泄漏", "冷却液从管路接头处泄漏"},
                {"刀塔换刀失败", "刀塔换刀时卡住或定位不准"},
                {"主轴温升过高", "主轴连续运行后温度超过报警值"},
                {"导轨磨损", "导轨表面出现明显磨损痕迹"},
                {"伺服驱动器报警", "伺服驱动器显示过载报警E-01"},
                {"尾座顶尖跳动", "尾座顶尖存在明显跳动"},
                {"卡盘夹紧力不足", "卡盘夹紧力下降，工件加工时松动"},
                {"主轴编码器信号异常", "主轴编码器反馈信号不稳定"},
                {"润滑系统报警", "集中润滑系统报警，油量不足或管路堵塞"},
                {"电气柜过温报警", "电气柜内部温度过高触发报警"},
                {"急停按钮失灵", "急停按钮按下后机床未停止"},
                {"Z轴反向间隙过大", "Z轴反向运动时存在过大间隙"},
                {"传送带打滑", "传送带运行时出现打滑现象"},
                {"气缸动作迟缓", "气缸动作速度明显下降"},
                {"电磁阀卡滞", "电磁阀无法正常开启或关闭"},
                {"温控系统失灵", "加热区温度无法精确控制"},
                {"变频器过流报警", "变频器频繁报过流故障"},
                {"PLC通讯中断", "PLC与上位机通讯频繁中断"},
                {"光栅尺读数跳变", "光栅尺反馈位置数据出现跳变"},
                {"液压缸内泄", "液压缸内部密封失效导致内泄漏"},
                {"焊接电弧不稳定", "焊接时电弧忽大忽小，焊缝质量差"},
                {"机器人关节异响", "机器人第三关节运动时有异响"},
                {"注塑机锁模力不足", "锁模时压力不够导致飞边"},
                {"激光功率衰减", "激光器输出功率逐渐衰减"},
                {"滚珠丝杠磨损", "滚珠丝杠间隙增大导致定位精度下降"},
                {"安全光幕误触发", "安全光幕频繁误报导致停机"},
                {"步进电机丢步", "步进电机运行时出现丢步现象"},
                {"冷却风扇故障", "散热风扇停转导致设备过温"},
                {"接地故障", "设备接地电阻超标"},
                {"行程开关失灵", "限位行程开关无法正常触发"},
                {"触摸屏无响应", "HMI触摸屏触摸无反应"},
                {"链条松弛", "传动链条松弛导致传动不平稳"},
                {"减速器异响", "减速器运转时发出异常声音"},
                {"继电器粘连", "继电器触点粘连无法断开"},
                {"皮带断裂", "传动皮带断裂导致停机"},
                {"联轴器松动", "联轴器连接松动产生振动"},
                {"压力开关失灵", "压力开关动作值偏差过大"}
        };

        String[] severities = {"轻微", "一般", "严重", "致命"};
        String[] categories = {"机械", "电气", "软件", "其他"};
        String[] reporters = {"张工", "李工", "王工", "赵工", "陈工"};

        List<Fault> allFaults = new ArrayList<>();
        for (int i = 0; i < faultData.length; i++) {
            String[] fd = faultData[i];
            Fault fault = Fault.builder()
                    .code("F" + String.format("%03d", i + 1))
                    .name(fd[0])
                    .description(fd[1])
                    .severity(severities[i % severities.length])
                    .category(categories[i % categories.length])
                    .occurrenceTime(LocalDateTime.now().minusDays(new Random(i + 100).nextInt(180)))
                    .reportedBy(reporters[i % reporters.length])
                    .solutions(new HashSet<>())
                    .build();

            fault.getSolutions().add(allSolutions.get(i));

            if (urlIndex < shuffledUrls.size() && i < 7) {
                fault.setImageUrls(List.of(shuffledUrls.get(urlIndex++)));
            }
            allFaults.add(fault);
        }
        log.info("构建 {} 个 Fault", allFaults.size());

        // ===== 3. 构建 Component（30个）并关联 Fault =====
        String[][] componentData = {
                {"主轴轴承", "BRG-7210AC", "角接触球轴承 50×90×20mm", "洛阳LYC", "12个月", "850.00"},
                {"X轴伺服电机", "SRV-HC-SFS152", "1.5kW 额定3000rpm", "三菱电机", "36个月", "4500.00"},
                {"液压泵", "HYD-PV2R2-65", "叶片泵 65mL/rev", "油研工业", "24个月", "3200.00"},
                {"冷却水管接头", "FIT-DN25-SS316", "不锈钢快接 DN25", "派克汉尼汾", "18个月", "120.00"},
                {"刀塔电机", "MOT-BL-500W", "无刷直流电机 500W", "汇川技术", "24个月", "2800.00"},
                {"主轴冷却器", "CLR-OIL-20L", "油冷机 20L/min", "好利旺", "36个月", "6500.00"},
                {"直线导轨", "GDE-HSR35A", "滚柱导轨 35mm", "THK", "24个月", "2200.00"},
                {"伺服驱动器", "DRV-MR-J4-200A", "2kW 伺服驱动器", "三菱电机", "36个月", "5800.00"},
                {"尾座顶尖", "TIP-MT5-R", "回转顶尖 MT5", "哈量", "12个月", "380.00"},
                {"液压卡盘", "CHK-K11-250", "三爪卡盘 250mm", "泰州华泰", "24个月", "1600.00"},
                {"主轴编码器", "ENC-ERN1387", "绝对值编码器 2048线", "海德汉", "36个月", "7200.00"},
                {"集中润滑泵", "LUB-AMR-II", "电动润滑泵 2L", "建河", "24个月", "1200.00"},
                {"电气柜散热风扇", "FAN-4715MS-23T", "轴流风扇 120mm 230V", "NMB", "18个月", "85.00"},
                {"急停按钮", "BTN-XB2-BS542", "蘑菇头急停按钮", "施耐德", "60个月", "45.00"},
                {"Z轴滚珠丝杠", "BSC-3210-C5", "丝杠 32×10mm C5级", "银泰PMI", "24个月", "3800.00"},
                {"传送带", "BLT-PVC-500", "PVC传送带 500mm宽", "双箭橡胶", "12个月", "1800.00"},
                {"气缸", "CYL-SC63-200", "标准气缸 φ63×200mm", "SMC", "24个月", "380.00"},
                {"电磁阀", "SOL-4V210-08", "二位五通电磁阀 1/4", "亚德客", "18个月", "120.00"},
                {"温控仪表", "TMP-E5CC-800", "温控器 0-800℃", "欧姆龙", "36个月", "560.00"},
                {"变频器", "VFD-ATV320-7.5kW", "通用变频器 7.5kW", "施耐德", "36个月", "3600.00"},
                {"PLC模块", "PLC-FX5U-64MT", "PLC主模块 32入32出", "三菱电机", "60个月", "4200.00"},
                {"光栅尺", "SCL-LF-485-1000", "线性光栅尺 1000mm 5μm", "发格", "36个月", "8500.00"},
                {"液压缸密封件", "SEL-YXD-80", "组合密封圈 φ80", "台湾NAK", "12个月", "65.00"},
                {"焊接送丝机", "WLD-KD-500", "送丝机构 MIG", "唐山松下", "24个月", "3500.00"},
                {"机器人减速器", "RED-RV-40E", "RV减速器 传动比121", "纳博特斯克", "36个月", "15000.00"},
                {"注塑机料筒", "BRL-60-L/D25", "料筒 φ60 L/D=25", "震雄", "12个月", "8000.00"},
                {"激光器", "LSR-IPG-4000", "光纤激光器 4kW", "IPG", "36个月", "120000.00"},
                {"滚珠丝杠螺母", "NUT-SFU2005", "螺母 SFU2005", "TBI", "18个月", "680.00"},
                {"安全光幕", "LGT-F3SG-4RE", "安全光幕 检测距离5m", "基恩士", "36个月", "4800.00"},
                {"步进电机", "STP-86BYG250H", "步进电机 8.5Nm", "雷赛智能", "24个月", "480.00"}
        };

        List<Component> allComponents = new ArrayList<>();
        int faultIdx = 0;
        for (int i = 0; i < componentData.length; i++) {
            String[] cd = componentData[i];
            Component component = Component.builder()
                    .name(cd[0])
                    .partNumber(cd[1])
                    .specification(cd[2])
                    .supplier(cd[3])
                    .lifecycle(cd[4])
                    .unitPrice(Double.parseDouble(cd[5]))
                    .causedFaults(new HashSet<>())
                    .build();

            // 每个 Component 关联 1~2 个 Fault
            if (faultIdx < allFaults.size()) {
                component.getCausedFaults().add(allFaults.get(faultIdx++));
            }
            if (i % 3 == 0 && faultIdx < allFaults.size()) {
                component.getCausedFaults().add(allFaults.get(faultIdx++));
            }

            if (urlIndex < shuffledUrls.size() && i < 5) {
                component.setImageUrls(List.of(shuffledUrls.get(urlIndex++)));
            }
            allComponents.add(component);
        }
        log.info("构建 {} 个 Component，已分配 {} 个 Fault", allComponents.size(), faultIdx);

        // ===== 4. 构建 Device（10个）并关联 Component =====
        String[][] deviceData = {
                {"数控车床CK6150", "DEV-001", "CK6150", "一号车间A区", "沈阳机床"},
                {"立式加工中心VMC850", "DEV-002", "VMC850", "一号车间B区", "大连机床"},
                {"液压折弯机WC67Y", "DEV-003", "WC67Y-100/3200", "二号车间A区", "扬力集团"},
                {"激光切割机GS-3015", "DEV-004", "GS-3015", "二号车间B区", "大族激光"},
                {"工业机器人IRB6700", "DEV-005", "IRB6700", "三号车间A区", "ABB"},
                {"注塑机MA3600", "DEV-006", "MA3600", "三号车间B区", "海天国际"},
                {"龙门铣床X2012", "DEV-007", "X2012", "四号车间A区", "齐齐哈尔二机床"},
                {"空压机GA37", "DEV-008", "GA37+", "动力车间", "阿特拉斯·科普柯"},
                {"电火花机EA12", "DEV-009", "EA12-Advance", "精密车间A区", "三菱电机"},
                {"平面磨床M7130", "DEV-010", "M7130", "四号车间B区", "上海机床"}
        };

        List<Device> allDevices = new ArrayList<>();
        int compIdx = 0;
        for (int i = 0; i < deviceData.length; i++) {
            String[] dd = deviceData[i];
            Device device = Device.builder()
                    .name(dd[0])
                    .code(dd[1])
                    .model(dd[2])
                    .location(dd[3])
                    .manufacturer(dd[4])
                    .purchaseDate(LocalDateTime.now().minusYears(1 + new Random(i + 200).nextInt(5)))
                    .ownedComponents(new HashSet<>())
                    .build();

            // 每个 Device 关联 3 个 Component
            for (int j = 0; j < 3 && compIdx < allComponents.size(); j++) {
                device.getOwnedComponents().add(allComponents.get(compIdx++));
            }

            if (urlIndex < shuffledUrls.size()) {
                device.setImageUrls(List.of(shuffledUrls.get(urlIndex++)));
            }
            allDevices.add(device);
        }
        log.info("构建 {} 个 Device，已分配 {} 个 Component", allDevices.size(), compIdx);

        // ===== 5. 生成向量 =====
        log.info("开始生成向量（此步骤耗时较长，请耐心等待）...");
        generateEmbeddings(allDevices, allComponents, allFaults, allSolutions);

        // ===== 6. 保存到 Neo4j =====
        log.info("开始保存到 Neo4j...");
        for (Device device : allDevices) {
            deviceRepository.save(device);
            log.info("已保存设备: {} ({})", device.getName(), device.getCode());
        }

        int totalEntities = allDevices.size() + allComponents.size() + allFaults.size() + allSolutions.size();
        log.info("===== 测试数据生成完成！共 {} 个实体，已使用 {} 张图片 =====", totalEntities, Math.min(urlIndex, shuffledUrls.size()));
    }

    private void generateEmbeddings(
            List<Device> devices,
            List<Component> components,
            List<Fault> faults,
            List<Solution> solutions
    ) {
        // Solution 无需生成向量（没有向量索引，不参与向量检索）
        log.info("Solution 跳过向量生成（无向量索引）");

        // Fault 向量（文本1536维 + 多模态1024维）
        for (int i = 0; i < faults.size(); i++) {
            Fault f = faults.get(i);
            try {
                String text = buildStringUtils.buildFaultEmbeddingText(f);
                // 文本向量（1536维，text-embedding-v4）
                List<Double> textVec = embeddingUtils.getEmbedding(text);
                f.setEmbedding(textVec);
                sleepBetweenApiCalls();
                // 多模态向量（1024维）：有图片传图片，没图片传文本
                boolean hasImages = f.getImageUrls() != null && !f.getImageUrls().isEmpty();
                List<Double> multiVec = multimodalEmbeddingUtils.getMultimodalEmbedding(
                        hasImages ? null : text,
                        hasImages ? f.getImageUrls() : null
                );
                f.setMultimodalEmbedding(multiVec);
                log.debug("故障[{}] 向量完成: {} (含图片={})", i, f.getName(), hasImages);
            } catch (Exception e) {
                log.warn("故障[{}] 向量失败: {} - {}", i, f.getName(), e.getMessage());
            }
            sleepBetweenApiCalls();
        }
        log.info("Fault 向量生成完成 ({}/{})", faults.size(), faults.size());

        // Component 向量（文本1536维 + 多模态1024维）
        for (int i = 0; i < components.size(); i++) {
            Component c = components.get(i);
            try {
                String text = buildStringUtils.buildComponentEmbeddingText(c);
                // 文本向量（1536维，text-embedding-v4）
                List<Double> textVec = embeddingUtils.getEmbedding(text);
                c.setEmbedding(textVec);
                sleepBetweenApiCalls();
                // 多模态向量（1024维）：有图片传图片，没图片传文本
                boolean hasImages = c.getImageUrls() != null && !c.getImageUrls().isEmpty();
                List<Double> multiVec = multimodalEmbeddingUtils.getMultimodalEmbedding(
                        hasImages ? null : text,
                        hasImages ? c.getImageUrls() : null
                );
                c.setMultimodalEmbedding(multiVec);
                log.debug("部件[{}] 向量完成: {} (含图片={})", i, c.getName(), hasImages);
            } catch (Exception e) {
                log.warn("部件[{}] 向量失败: {} - {}", i, c.getName(), e.getMessage());
            }
            sleepBetweenApiCalls();
        }
        log.info("Component 向量生成完成 ({}/{})", components.size(), components.size());

        // Device 无需生成向量（没有向量索引，通过关键字模糊匹配检索）
        log.info("Device 跳过向量生成（无向量索引）");
    }

    private void sleepBetweenApiCalls() {
        try {
            Thread.sleep(500);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
