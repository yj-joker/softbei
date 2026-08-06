package ai.weixiu.service.impl;

import ai.weixiu.entity.KnowledgeDocument;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.entity.ManualDevice;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.exception.FormatErrorException;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.exception.NullException;
import ai.weixiu.mapper.KnowledgeDocumentMapper;
import ai.weixiu.mapper.MaintenanceManualMapper;
import ai.weixiu.mq.KnowledgeImportProducer;
import ai.weixiu.service.KnowledgeDocumentService;
import ai.weixiu.service.MioIOUpLoadService;
import ai.weixiu.service.ExpirationService;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeUnit;

@Service
@Slf4j
@RequiredArgsConstructor
public class KnowledgeDocumentServiceImpl
        extends ServiceImpl<KnowledgeDocumentMapper, KnowledgeDocument>
        implements KnowledgeDocumentService {

    /** 文件大小上限：50MB */
    private static final long MAX_FILE_SIZE = 50 * 1024 * 1024;

    /** 版本号分配锁前缀，按 manualId 加锁保证串行 */
    private static final String VERSION_LOCK_PREFIX = "knowledge:version:lock:";

    private static final List<String> FILE_EXTENSIONS = List.of(".pdf");
    private static final List<String> FILE_CONTENT_TYPES = List.of(
            "application/pdf",
            "application/octet-stream"
    );

    private final MaintenanceManualMapper manualMapper;
    private final MioIOUpLoadService mioIOUpLoadService;
    private final KnowledgeImportProducer knowledgeImportProducer;
    private final RedissonClient redissonClient;
    private final ExpirationService expirationService;
    private final ai.weixiu.mapper.ManualDeviceMapper manualDeviceMapper;

    @Override
    @Transactional
    public KnowledgeDocument uploadNewVersion(Long manualId, MultipartFile file) {
        // 1. 校验手册存在
        MaintenanceManual manual = manualMapper.selectById(manualId);
        if (manual == null) {
            throw new NotFoundException("手册不存在");
        }

        // 2. 校验文件
        validateFile(file);

        // 3. 分布式锁保证同一手册版本号串行分配，防止并发上传产生重复版本号
        RLock versionLock = redissonClient.getLock(VERSION_LOCK_PREFIX + manualId);
        boolean locked = false;
        try {
            locked = versionLock.tryLock(5, 30, TimeUnit.SECONDS);
            if (!locked) {
                throw new RuntimeException("获取版本号锁超时，请稍后重试");
            }
            return doUploadNewVersion(manual, file);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("获取版本号锁被中断", e);
        } finally {
            if (locked && versionLock.isHeldByCurrentThread()) {
                versionLock.unlock();
            }
        }
    }

    /**
     * 在持有版本号锁的情况下执行上传逻辑。
     * 锁内完成：查最大版本号 → 插入新记录 → 发 MQ 消息。
     */
    private KnowledgeDocument doUploadNewVersion(MaintenanceManual manual, MultipartFile file) {
        Long manualId = manual.getId();

        // 计算版本号：当前最大版本号 + 1
        Integer maxVersion = baseMapper.selectObjs(
                new LambdaQueryWrapper<KnowledgeDocument>()
                        .select(KnowledgeDocument::getVersion)
                        .eq(KnowledgeDocument::getManualId, manualId)
                        .orderByDesc(KnowledgeDocument::getVersion)
                        .last("LIMIT 1")
        ).stream().findFirst().map(o -> (Integer) o).orElse(0);
        int newVersion = maxVersion + 1;

        // 上传文件到 MinIO 私有桶
        String objectName = mioIOUpLoadService.getObjectName(file, BucketEnum.PRIVATE.getName());

        // 创建 knowledge_document 记录
        Long docId = IdWorker.getId();
        String documentId = "kdoc_" + docId;

        KnowledgeDocument doc = new KnowledgeDocument();
        doc.setId(docId);
        doc.setManualId(manualId);
        doc.setDocumentId(documentId);
        doc.setVersion(newVersion);
        doc.setFileName(file.getOriginalFilename());
        doc.setFileType(getFileSuffix(file));
        doc.setFileSize(file.getSize());
        doc.setMinioObjectName(objectName);
        doc.setStatus("pending");
        doc.setTextCount(0);
        doc.setImageCount(0);
        doc.setTableCount(0);
        doc.setCreatedById(BaseContext.getCurrentId());
        LocalDateTime now = LocalDateTime.now();
        doc.setCreatedAt(now);
        doc.setUpdatedAt(now);
        save(doc);

        // 更新手册状态为"处理中"（不改 activeDocumentId，旧版本继续可用）
        manual.setStatus(2);
        manual.setUpdatedAt(now);
        manualMapper.updateById(manual);

        // 生成文件预签名 URL 给 Python 下载
        String fileUrl = mioIOUpLoadService.getPresignedUrl(objectName, BucketEnum.PRIVATE, 120);
        String oldDocumentId = resolveActiveDocumentId(manual);
        Map<String, Object> documentIdentity = resolveDocumentIdentity(manualId);
        String deviceName = Objects.toString(documentIdentity.get("device_name"), null);

        log.info("上传新版本: manualId={}, version={}, documentId={}", manualId, newVersion, documentId);

        // 事务提交后再发送 MQ 消息。oldDocumentId 只用于 revision 继承与差异分析，
        // 旧向量仍由 onParseSuccess 在新版本成功切换后删除。
        final Long currentUserId = BaseContext.getCurrentId();
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                knowledgeImportProducer.sendImportTask(
                        documentId,
                        fileUrl,
                        doc.getFileType().replace(".", ""),
                        null,
                        currentUserId,
                        "v" + newVersion,
                        deviceName,
                        null,
                        oldDocumentId,
                        manualId,
                        documentIdentity
                );
            }
        });
        return doc;
    }

    @Override
    @Transactional
    public void onParseSuccess(String documentId, Map<String, Object> data) {
        KnowledgeDocument doc = getByDocumentId(documentId);

        // 更新 knowledge_document 状态和统计
        doc.setStatus("ready");
        doc.setTextCount(toInt(data.get("text_count")));
        doc.setImageCount(toInt(data.get("image_count")));
        doc.setTableCount(toInt(data.get("table_count")));
        doc.setUpdatedAt(LocalDateTime.now());
        updateById(doc);

        // 切换 maintenance_manual 的 active 版本（仅当回调文档严格新于当前 active 时才切换）
        // 行锁一直持有到 @Transactional 方法提交，避免 v2/v3 并发回调读取同一旧 active
        // 后由较旧回调覆盖较新 active，并误清理新版本资源。
        MaintenanceManual manual = manualMapper.selectByIdForUpdate(doc.getManualId());
        if (manual != null) {
            boolean shouldActivate = true;
            String oldDocumentId = null;
            String oldMinioObjectName = null;

            if (manual.getActiveDocumentId() != null) {
                KnowledgeDocument activeDoc = getById(manual.getActiveDocumentId());
                if (activeDoc != null) {
                    boolean sameDocument = Objects.equals(activeDoc.getId(), doc.getId())
                            || Objects.equals(activeDoc.getDocumentId(), doc.getDocumentId());
                    if (sameDocument) {
                        // 成功回调可能重复投递；当前文档已 active 时不得把自己当旧版本删除。
                        shouldActivate = false;
                        log.info("跳过重复成功回调: documentId={} 已是当前 active", documentId);
                    } else if (activeDoc.getVersion() >= doc.getVersion()) {
                        // 当前 active 版本不旧于回调版本，不切换也不清理。
                        shouldActivate = false;
                        log.info("跳过 active 切换: 当前 active v{} >= 回调 v{}, documentId={}",
                                activeDoc.getVersion(), doc.getVersion(), documentId);
                    } else {
                        // 记录旧版本信息，成功切换后再清理
                        oldDocumentId = activeDoc.getDocumentId();
                        oldMinioObjectName = activeDoc.getMinioObjectName();
                    }
                }
            }

            if (shouldActivate) {
                // 切换 active 版本
                manual.setActiveDocumentId(doc.getId());
                manual.setStatus(1);
                // 回写新版本的文件信息到 maintenance_manual
                manual.setFileName(doc.getFileName());
                manual.setFileType(doc.getFileType());
                manual.setFileSize(doc.getFileSize());
                manual.setMinioObjectName(doc.getMinioObjectName());
                manual.setUpdatedAt(LocalDateTime.now());
                manualMapper.updateById(manual);

                // 事务提交后：无条件触发 KG 抽取 + 过期判定；仅在有旧版本时清理旧资源。
                // 注意：KG 抽取必须每次导入都触发（含首次导入 oldDocumentId=null 的场景），
                // 不能包在"有旧版本"条件里——否则首次导入永远不入图谱。
                final String finalOldDocumentId = oldDocumentId;
                final String finalOldMinioObjectName = oldMinioObjectName;
                TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                    @Override
                    public void afterCommit() {
                        // 2. 触发图谱知识过期判定 + chunk 级别 KG 同步
                        // finalOldDocumentId 已有：用于 chunk diff；无旧版本时只做文档级过期判定
                        try {
                            String deviceHint = resolveDeviceHint(doc.getManualId());
                            expirationService.checkManualUpgradeAsync(
                                    doc.getManualId(),
                                    documentId,
                                    finalOldDocumentId,
                                    manual.getManualName(),
                                    deviceHint,
                                    () -> {
                                        if (finalOldDocumentId == null) return;
                                        try {
                                            knowledgeImportProducer.sendDeleteTask(finalOldDocumentId);
                                            log.info("已发送旧版本向量删除任务: oldDocumentId={}", finalOldDocumentId);
                                        } catch (Exception e) {
                                            log.warn("发送旧版本向量删除消息失败: oldDocumentId={}", finalOldDocumentId, e);
                                        }
                                        if (finalOldMinioObjectName != null) {
                                            try {
                                                mioIOUpLoadService.delete(finalOldMinioObjectName, BucketEnum.PRIVATE);
                                                log.info("已删除旧版本 MinIO 文件: {}", finalOldMinioObjectName);
                                            } catch (Exception e) {
                                                log.warn("删除旧版本 MinIO 文件失败: {}", finalOldMinioObjectName, e);
                                            }
                                        }
                                    });
                        } catch (Exception e) {
                            log.warn("触发图谱过期判定失败（非阻塞）: manualId={}, err={}",
                                    doc.getManualId(), e.getMessage());
                        }

                    }
                });
            }
        }

        log.info("解析成功: documentId={}, version={}, text={}, image={}, table={}",
                documentId, doc.getVersion(), doc.getTextCount(), doc.getImageCount(), doc.getTableCount());
    }

    /**
     * 取手册关联的"适用设备"名作为 KG 抽取的设备锚点。
     * <p>
     * 用户上传时选的设备决定图谱设备归属：抽取锚定到该设备名，同设备多手册 MERGE 复用节点。
     * 手册可关联多个设备，但图谱设备根节点只能有一个 → 取第一个关联设备名。
     * 未关联任何设备 → 返回空串，抽取回退到 LLM 自行识别设备名。
     */
    private String resolveDeviceHint(Long manualId) {
        return Objects.toString(resolveDocumentIdentity(manualId).get("device_name"), "");
    }

    /**
     * 将用户在 manual_device 中确认的适用设备构造成开放词汇文档身份。
     * 第一项为主设备名，其余去重后仅作为当前文档 aliases。
     */
    private Map<String, Object> resolveDocumentIdentity(Long manualId) {
        if (manualId == null) return Map.of();
        try {
            List<ManualDevice> links = manualDeviceMapper.selectList(
                    new LambdaQueryWrapper<ManualDevice>()
                            .eq(ManualDevice::getManualId, manualId)
                            .orderByAsc(ManualDevice::getId));
            LinkedHashSet<String> names = new LinkedHashSet<>();
            for (ManualDevice link : links) {
                if (link.getDeviceName() != null && !link.getDeviceName().isBlank()) {
                    names.add(link.getDeviceName().trim());
                }
            }
            if (names.isEmpty()) return Map.of();

            List<String> orderedNames = new ArrayList<>(names);
            Map<String, Object> identity = new LinkedHashMap<>();
            identity.put("device_name", orderedNames.getFirst());
            identity.put("aliases", List.copyOf(orderedNames.subList(1, orderedNames.size())));
            identity.put("confidence", 1.0);
            identity.put("identity_source", "user_metadata");
            return Map.copyOf(identity);
        } catch (Exception e) {
            log.warn("查询手册关联设备失败（回退LLM识别）: manualId={}, err={}", manualId, e.getMessage());
        }
        return Map.of();
    }

    /** 获取上传开始时仍在使用的 active 文档业务 ID。 */
    private String resolveActiveDocumentId(MaintenanceManual manual) {
        if (manual == null || manual.getActiveDocumentId() == null) return null;
        KnowledgeDocument activeDocument = getById(manual.getActiveDocumentId());
        if (activeDocument == null || activeDocument.getDocumentId() == null
                || activeDocument.getDocumentId().isBlank()) {
            log.warn("手册 active 文档记录缺失: manualId={}, activeDocumentId={}",
                    manual.getId(), manual.getActiveDocumentId());
            return null;
        }
        return activeDocument.getDocumentId().trim();
    }

    @Override
    @Transactional
    public void onParseFailed(String documentId, String errorMessage) {
        KnowledgeDocument doc = getByDocumentId(documentId);

        doc.setStatus("failed");
        doc.setErrorMessage(errorMessage);
        doc.setUpdatedAt(LocalDateTime.now());
        updateById(doc);

        // 不改 active_document_id，旧版本继续可用
        // 但如果是首次上传失败（manual 没有 active），把 status 改回 0
        MaintenanceManual manual = manualMapper.selectById(doc.getManualId());
        if (manual != null) {
            if (manual.getActiveDocumentId() == null) {
                manual.setStatus(0);
            } else {
                manual.setStatus(1);
            }
            manual.setUpdatedAt(LocalDateTime.now());
            manualMapper.updateById(manual);
        }

        log.error("解析失败: documentId={}, error={}", documentId, errorMessage);
    }

    @Override
    public List<KnowledgeDocument> listVersions(Long manualId) {
        return list(new LambdaQueryWrapper<KnowledgeDocument>()
                .eq(KnowledgeDocument::getManualId, manualId)
                .orderByDesc(KnowledgeDocument::getVersion));
    }

    @Override
    public KnowledgeDocument getLatestVersion(Long manualId) {
        return getOne(new LambdaQueryWrapper<KnowledgeDocument>()
                .eq(KnowledgeDocument::getManualId, manualId)
                .orderByDesc(KnowledgeDocument::getVersion)
                .last("LIMIT 1"));
    }

    // ===== 内部方法 =====

    private KnowledgeDocument getByDocumentId(String documentId) {
        KnowledgeDocument doc = getOne(new LambdaQueryWrapper<KnowledgeDocument>()
                .eq(KnowledgeDocument::getDocumentId, documentId));
        if (doc == null) {
            throw new NotFoundException("文档版本不存在: " + documentId);
        }
        return doc;
    }

    private void validateFile(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new NullException("文件不能为空");
        }
        if (file.getSize() > MAX_FILE_SIZE) {
            throw new FormatErrorException("文件大小不能超过 50MB");
        }
        String fileSuffix = getFileSuffix(file).toLowerCase(Locale.ROOT);
        String contentType = file.getContentType();
        boolean validExtension = FILE_EXTENSIONS.contains(fileSuffix);
        boolean validContentType = contentType != null
                && FILE_CONTENT_TYPES.contains(contentType.toLowerCase(Locale.ROOT));
        if (!validExtension || !validContentType) {
            throw new FormatErrorException("仅支持 PDF 文件");
        }
    }

    private String getFileSuffix(MultipartFile file) {
        String originalFilename = Objects.requireNonNull(file.getOriginalFilename());
        if (!originalFilename.contains(".")) {
            throw new FormatErrorException("文件缺少扩展名");
        }
        return originalFilename.substring(originalFilename.lastIndexOf("."));
    }

    private int toInt(Object value) {
        if (value == null) return 0;
        if (value instanceof Number) return ((Number) value).intValue();
        try { return Integer.parseInt(value.toString()); } catch (NumberFormatException e) { return 0; }
    }
}
