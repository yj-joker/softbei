package ai.weixiu.service.impl;

import ai.weixiu.entity.KnowledgeDocument;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.entity.ManualDevice;
import ai.weixiu.entity.ManualReadRecord;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.exception.NullException;
import ai.weixiu.mapper.ManualDeviceMapper;
import ai.weixiu.mapper.ManualReadRecordMapper;
import ai.weixiu.mapper.MaintenanceManualMapper;
import ai.weixiu.mq.KnowledgeImportProducer;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.MaintenanceManualDTO;
import ai.weixiu.pojo.query.MaintenanceManualQuery;
import ai.weixiu.pojo.vo.MaintenanceManualVO;
import ai.weixiu.entity.Device;
import ai.weixiu.service.DeviceService;
import ai.weixiu.service.KnowledgeDocumentService;
import ai.weixiu.service.MaintenanceManualService;
import ai.weixiu.service.MioIOUpLoadService;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.stream.Collectors;

@Service
@AllArgsConstructor
@Slf4j
public class MaintenanceManualServiceImpl
        extends ServiceImpl<MaintenanceManualMapper, MaintenanceManual>
        implements MaintenanceManualService {

    /** 手册不存在提示。 */
    private static final String MANUAL_NOT_FOUND = "维修手册不存在";

    /** 私有桶文档预签名地址有效时长（分钟）。 */
    private static final int PRIVATE_FILE_URL_EXPIRY_MINUTES = 120;

    private final MioIOUpLoadService mioIOUpLoadService;
    private final KnowledgeDocumentService knowledgeDocumentService;
    private final KnowledgeImportProducer knowledgeImportProducer;
    private final ManualDeviceMapper manualDeviceMapper;
    private final ManualReadRecordMapper manualReadRecordMapper;
    private final DeviceService deviceService;
    /** 直接用 Neo4j Driver 开独立 session：afterCommit 时 Spring 事务已提交，不能复用 Neo4jClient 的事务 session。 */
    private final org.neo4j.driver.Driver neo4jDriver;

    @Override
    @Transactional
    public MaintenanceManual add(MaintenanceManualDTO maintenanceManualDTO, MultipartFile file) {
        // 1. 保存手册元数据（不含文件信息）
        MaintenanceManual manual = new MaintenanceManual();
        BeanUtils.copyProperties(maintenanceManualDTO, manual, "id");
        manual.setId(IdWorker.getId());
        manual.setStatus(2); // 处理中
        manual.setCreatedById(BaseContext.getCurrentId());
        LocalDateTime now = LocalDateTime.now();
        manual.setCreatedAt(now);
        manual.setUpdatedAt(now);
        save(manual);

        // 2. 写入手册-设备关联（管理员显式多选；通用手册可不选）
        syncManualDevices(manual.getId(), maintenanceManualDTO.getDeviceIds());

        // 3. 上传第一版文档（文件信息将在 onParseSuccess 回调中统一回写到 manual）
        knowledgeDocumentService.uploadNewVersion(manual.getId(), file);

        log.info("新增手册成功: {}, 已发起 v1 解析", manual.getId());
        return manual;
    }

    /**
     * 全量同步手册-设备关联（manual_device）。
     *
     * <p>语义：传入的 deviceIds 即为该手册的最终适用设备集合——先清空旧关联再按列表重建，
     * 设备名从 Neo4j Device 节点按 id 解析（找不到的 id 跳过）。</p>
     *
     * @param deviceIds 适用设备 UUID 列表；空列表表示清空关联（通用手册）
     */
    private void syncManualDevices(Long manualId, List<String> deviceIds) {
        // 先清空旧关联（全量覆盖语义）
        manualDeviceMapper.delete(new LambdaQueryWrapper<ManualDevice>()
                .eq(ManualDevice::getManualId, manualId));

        if (deviceIds == null || deviceIds.isEmpty()) {
            return;
        }

        LocalDateTime now = LocalDateTime.now();
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (String deviceId : deviceIds) {
            if (!StringUtils.hasText(deviceId) || !seen.add(deviceId)) {
                continue; // 跳过空值与重复 id
            }
            Device device = deviceService.findById(deviceId).orElse(null);
            if (device == null) {
                log.warn("[手册-设备] 设备不存在，跳过关联 manualId={}, deviceId={}", manualId, deviceId);
                continue;
            }
            ManualDevice md = new ManualDevice();
            md.setManualId(manualId);
            md.setDeviceId(deviceId);
            md.setDeviceName(device.getName());
            md.setCreatedAt(now);
            manualDeviceMapper.insert(md);
            log.info("[手册-设备] 关联 manualId={}, deviceId={}", manualId, deviceId);
        }
    }

    @Override
    @Transactional
    public void deleteById(Long id) {
        MaintenanceManual manual = getManualById(id);

        // 收集需要在事务提交后清理的资源信息
        List<KnowledgeDocument> versions = knowledgeDocumentService.listVersions(id);
        List<String> documentIdsToDelete = new java.util.ArrayList<>();
        List<String> minioObjectsToDelete = new java.util.ArrayList<>();

        for (KnowledgeDocument doc : versions) {
            if (StringUtils.hasText(doc.getDocumentId())) {
                documentIdsToDelete.add(doc.getDocumentId());
            }
            if (StringUtils.hasText(doc.getMinioObjectName())) {
                minioObjectsToDelete.add(doc.getMinioObjectName());
            }
            // 事务内只做 DB 删除
            knowledgeDocumentService.removeById(doc.getId());
        }

        // 兼容旧数据：旧字段指向的 MinIO 文件也需要清理
        if (StringUtils.hasText(manual.getMinioObjectName())) {
            minioObjectsToDelete.add(manual.getMinioObjectName());
        }

        // 删除手册-设备关联
        manualDeviceMapper.delete(new LambdaQueryWrapper<ManualDevice>()
                .eq(ManualDevice::getManualId, id));

        // 删除阅读记录
        manualReadRecordMapper.delete(new LambdaQueryWrapper<ManualReadRecord>()
                .eq(ManualReadRecord::getManualId, id));

        removeById(id);
        log.info("删除手册成功: {}, 共删除 {} 个版本", id, versions.size());

        // 事务提交后再执行不可逆的副作用（MQ 消息 + MinIO 文件删除）
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                for (String documentId : documentIdsToDelete) {
                    try {
                        knowledgeImportProducer.sendDeleteTask(documentId);
                    } catch (Exception e) {
                        log.warn("发送向量删除消息失败: documentId={}", documentId, e);
                    }
                }
                for (String objectName : minioObjectsToDelete) {
                    try {
                        mioIOUpLoadService.delete(objectName, BucketEnum.PRIVATE);
                    } catch (Exception e) {
                        log.warn("删除 MinIO 文件失败: {}", objectName, e);
                    }
                }
                // 分级安全清理图谱节点（按 manual_id 归属，保护一线沉淀经验）
                try {
                    deleteGraphNodesByManual(id);
                } catch (Exception e) {
                    log.warn("删除手册图谱节点失败（非阻塞）: manualId={}", id, e);
                }
            }
        });
    }

    /**
     * 手册删除时分级安全清理图谱节点（按 manual_id 归属）。
     * <p>
     * 原则：绝不误删一线沉淀经验。步骤：
     * ① 从所有归属本手册的节点的 manual_ids 移除本 id；
     * ② 仅删除"manual_ids 变空 + 自身非沉淀(非verified/无source_task_id) + 下游无沉淀"的节点；
     * ③ 有沉淀牵连或被其他手册共享的节点保留。
     * 沉淀特征：verified=true 或 source_task_id 非空（见 promoteToGraph）。
     */
    private void deleteGraphNodesByManual(Long manualId) {
        if (manualId == null) return;

        // 用 Driver 开独立 session：此时 Spring 的 MySQL 事务已提交（afterCommit），
        // 不能复用 Neo4jClient 绑定的事务 session，否则报 "transaction has been committed"。
        try (org.neo4j.driver.Session session = neo4jDriver.session()) {
            // Step 1: 从 manual_ids 移除本手册 id
            session.run("""
                    MATCH (n)
                    WHERE $manualId IN coalesce(n.manual_ids, [])
                    SET n.manual_ids = [x IN n.manual_ids WHERE x <> $manualId]
                    """,
                    org.neo4j.driver.Values.parameters("manualId", manualId));

            // Step 2: 删除可安全删除的节点（manual_ids 空 + 非沉淀 + 下游无沉淀）
            long deleted = session.run("""
                    MATCH (n)
                    WHERE (n:Device OR n:Component OR n:Fault OR n:Solution)
                      AND size(coalesce(n.manual_ids, [])) = 0
                      AND coalesce(n.verified, false) = false
                      AND n.source_task_id IS NULL
                      AND NOT EXISTS {
                          MATCH (n)-[*1..3]->(m)
                          WHERE coalesce(m.verified, false) = true OR m.source_task_id IS NOT NULL
                      }
                    WITH collect(n) AS delNodes, count(n) AS delCnt
                    FOREACH (x IN delNodes | DETACH DELETE x)
                    RETURN delCnt
                    """)
                    .single().get("delCnt").asLong(0);

            log.info("手册图谱节点清理: manualId={}, 已删除节点数={}（含沉淀/共享的节点已保留）", manualId, deleted);
        }
    }

    @Override
    @Transactional
    public MaintenanceManual update(MaintenanceManualDTO maintenanceManualDTO, MultipartFile file) {
        if (maintenanceManualDTO.getId() == null) {
            throw new NullException("手册 ID 不能为空");
        }

        MaintenanceManual manual = getManualById(maintenanceManualDTO.getId());

        // 更新元数据字段
        if (StringUtils.hasText(maintenanceManualDTO.getManualName())) {
            manual.setManualName(maintenanceManualDTO.getManualName());
        }
        if (maintenanceManualDTO.getManualImage() != null) {
            manual.setManualImage(maintenanceManualDTO.getManualImage());
        }
        if (maintenanceManualDTO.getManualDesc() != null) {
            manual.setManualDesc(maintenanceManualDTO.getManualDesc());
        }

        manual.setUpdatedAt(LocalDateTime.now());
        updateById(manual);

        // 同步手册-设备关联：deviceIds 为 null 表示本次未携带（不动），否则全量覆盖
        if (maintenanceManualDTO.getDeviceIds() != null) {
            syncManualDevices(manual.getId(), maintenanceManualDTO.getDeviceIds());
        }

        // 有新文件时上传新版本
        if (file != null && !file.isEmpty()) {
            knowledgeDocumentService.uploadNewVersion(manual.getId(), file);
            log.info("更新手册并上传新版本: {}", manual.getId());
        } else {
            log.info("更新手册元数据: {}", manual.getId());
        }

        return manual;
    }

    @Override
    public MaintenanceManual getManualById(Long id) {
        if (id == null) {
            throw new NullException("手册 ID 不能为空");
        }
        MaintenanceManual manual = getById(id);
        if (manual == null) {
            throw new NotFoundException(MANUAL_NOT_FOUND);
        }
        return manual;
    }

    @Override
    public MaintenanceManualVO getManualDetailById(Long id) {
        MaintenanceManual manual = getManualById(id);
        MaintenanceManualVO vo = new MaintenanceManualVO();
        BeanUtils.copyProperties(manual, vo);

        // 从 active KnowledgeDocument 获取文件信息
        if (manual.getActiveDocumentId() != null) {
            KnowledgeDocument activeDoc = knowledgeDocumentService.getById(manual.getActiveDocumentId());
            if (activeDoc != null) {
                vo.setFileName(activeDoc.getFileName());
                vo.setFileType(activeDoc.getFileType());
                vo.setFileSize(activeDoc.getFileSize());
                vo.setActiveVersion(activeDoc.getVersion());
                vo.setTextCount(activeDoc.getTextCount());
                vo.setImageCount(activeDoc.getImageCount());
                vo.setTableCount(activeDoc.getTableCount());

                if (StringUtils.hasText(activeDoc.getMinioObjectName())) {
                    vo.setFileUrl(mioIOUpLoadService.getPresignedUrl(
                            activeDoc.getMinioObjectName(),
                            BucketEnum.PRIVATE,
                            PRIVATE_FILE_URL_EXPIRY_MINUTES
                    ));
                }
            }
        } else {
            // 兼容旧数据：active_document_id 为空时走原来的逻辑
            if (StringUtils.hasText(manual.getMinioObjectName())) {
                vo.setFileUrl(mioIOUpLoadService.getPresignedUrl(
                        manual.getMinioObjectName(),
                        BucketEnum.PRIVATE,
                        PRIVATE_FILE_URL_EXPIRY_MINUTES
                ));
            }
        }

        // 最新版本的解析状态
        KnowledgeDocument latestDoc = knowledgeDocumentService.getLatestVersion(id);
        if (latestDoc != null) {
            vo.setParseStatus(latestDoc.getStatus());
            vo.setParseErrorMessage(latestDoc.getErrorMessage());
        }

        // 版本总数
        List<KnowledgeDocument> versions = knowledgeDocumentService.listVersions(id);
        vo.setTotalVersions(versions.size());

        // 关联设备
        List<ManualDevice> manualDevices = manualDeviceMapper.selectList(
                new LambdaQueryWrapper<ManualDevice>().eq(ManualDevice::getManualId, id));
        List<MaintenanceManualVO.DeviceSimple> deviceList = new ArrayList<>();
        for (ManualDevice md : manualDevices) {
            MaintenanceManualVO.DeviceSimple ds = new MaintenanceManualVO.DeviceSimple();
            ds.setDeviceId(md.getDeviceId());
            ds.setDeviceName(md.getDeviceName());
            deviceList.add(ds);
        }
        vo.setDevices(deviceList);

        return vo;
    }

    @Override
    public PageResult<MaintenanceManualVO> getManualList(MaintenanceManualQuery query) {
        Integer pageNum = query.getPage() == null ? 1 : query.getPage();
        Integer pageSize = query.getSize() == null ? 10 : query.getSize();
        Page<MaintenanceManual> page = new Page<>(pageNum, pageSize);
        LambdaQueryWrapper<MaintenanceManual> wrapper = new LambdaQueryWrapper<>();
        wrapper.like(StringUtils.hasText(query.getManualName()), MaintenanceManual::getManualName, query.getManualName())
                .eq(query.getStatus() != null, MaintenanceManual::getStatus, query.getStatus())
                .orderBy(true, !Objects.equals(query.getIsAsc(), 1), MaintenanceManual::getCreatedAt);
        Page<MaintenanceManual> result = page(page, wrapper);

        List<MaintenanceManualVO> voList = result.getRecords().stream().map(manual -> {
            MaintenanceManualVO vo = new MaintenanceManualVO();
            BeanUtils.copyProperties(manual, vo);
            if (StringUtils.hasText(manual.getMinioObjectName())) {
                vo.setFileUrl(mioIOUpLoadService.getPresignedUrl(
                        manual.getMinioObjectName(),
                        BucketEnum.PRIVATE,
                        PRIVATE_FILE_URL_EXPIRY_MINUTES
                ));
            }
            return vo;
        }).collect(Collectors.toList());

        return new PageResult<>(voList, result.getTotal(), pageNum, pageSize);
    }

}
