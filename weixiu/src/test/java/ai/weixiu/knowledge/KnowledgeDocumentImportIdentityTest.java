package ai.weixiu.knowledge;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.entity.KnowledgeDocument;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.entity.ManualDevice;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.mapper.KnowledgeDocumentMapper;
import ai.weixiu.mapper.MaintenanceManualMapper;
import ai.weixiu.mapper.ManualDeviceMapper;
import ai.weixiu.mq.KnowledgeImportProducer;
import ai.weixiu.service.ExpirationService;
import ai.weixiu.service.MioIOUpLoadService;
import ai.weixiu.service.impl.KnowledgeDocumentServiceImpl;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class KnowledgeDocumentImportIdentityTest {

    @AfterEach
    void clearTransactionSynchronization() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
    }

    @Test
    void uploadPublishesConfirmedDeviceIdentityAndActiveDocumentIdAfterCommit() throws Exception {
        MybatisConfiguration configuration = new MybatisConfiguration();
        TableInfoHelper.initTableInfo(
                new MapperBuilderAssistant(configuration, "knowledge-document-import-test"),
                KnowledgeDocument.class
        );
        TableInfoHelper.initTableInfo(
                new MapperBuilderAssistant(configuration, "manual-device-import-test"),
                ManualDevice.class
        );
        KnowledgeDocumentMapper documentMapper = mock(KnowledgeDocumentMapper.class);
        MaintenanceManualMapper manualMapper = mock(MaintenanceManualMapper.class);
        ManualDeviceMapper manualDeviceMapper = mock(ManualDeviceMapper.class);
        MioIOUpLoadService storage = mock(MioIOUpLoadService.class);
        RedissonClient redisson = mock(RedissonClient.class);
        RLock lock = mock(RLock.class);
        RabbitTemplate rabbitTemplate = mock(RabbitTemplate.class);
        KnowledgeImportProducer producer = new KnowledgeImportProducer(rabbitTemplate);

        KnowledgeDocumentServiceImpl service = new KnowledgeDocumentServiceImpl(
                manualMapper,
                storage,
                producer,
                redisson,
                mock(ExpirationService.class),
                manualDeviceMapper
        );
        ReflectionTestUtils.setField(service, "baseMapper", documentMapper);

        MaintenanceManual manual = new MaintenanceManual()
                .setId(20L)
                .setManualName("设备维修手册")
                .setActiveDocumentId(101L)
                .setStatus(1);
        KnowledgeDocument activeDocument = new KnowledgeDocument()
                .setId(101L)
                .setManualId(20L)
                .setDocumentId("kdoc_previous")
                .setVersion(2)
                .setStatus("ready");
        List<ManualDevice> confirmedDevices = List.of(
                new ManualDevice().setId(1L).setManualId(20L).setDeviceName("设备甲"),
                new ManualDevice().setId(2L).setManualId(20L).setDeviceName("设备乙"),
                new ManualDevice().setId(3L).setManualId(20L).setDeviceName("设备甲")
        );
        MultipartFile file = mock(MultipartFile.class);

        when(manualMapper.selectById(20L)).thenReturn(manual);
        when(documentMapper.selectObjs(any())).thenReturn(List.of(2));
        when(documentMapper.selectById(101L)).thenReturn(activeDocument);
        when(documentMapper.insert(any(KnowledgeDocument.class))).thenReturn(1);
        when(manualDeviceMapper.selectList(any())).thenReturn(confirmedDevices);
        when(redisson.getLock("knowledge:version:lock:20")).thenReturn(lock);
        when(lock.tryLock(5, 30, java.util.concurrent.TimeUnit.SECONDS)).thenReturn(true);
        when(lock.isHeldByCurrentThread()).thenReturn(true);
        when(file.isEmpty()).thenReturn(false);
        when(file.getSize()).thenReturn(1024L);
        when(file.getOriginalFilename()).thenReturn("manual.pdf");
        when(file.getContentType()).thenReturn("application/pdf");
        when(storage.getObjectName(file, BucketEnum.PRIVATE.getName())).thenReturn("manuals/manual.pdf");
        when(storage.getPresignedUrl("manuals/manual.pdf", BucketEnum.PRIVATE, 120))
                .thenReturn("https://example.invalid/manual.pdf");

        TransactionSynchronizationManager.initSynchronization();
        service.uploadNewVersion(20L, file);

        List<TransactionSynchronization> synchronizations =
                TransactionSynchronizationManager.getSynchronizations();
        assertEquals(1, synchronizations.size());
        synchronizations.forEach(TransactionSynchronization::afterCommit);

        ArgumentCaptor<Object> payloadCaptor = ArgumentCaptor.forClass(Object.class);
        verify(rabbitTemplate, times(1)).convertAndSend(
                eq(RabbitMQConfig.KNOWLEDGE_EXCHANGE),
                eq(RabbitMQConfig.KNOWLEDGE_IMPORT_KEY),
                payloadCaptor.capture()
        );
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = (Map<String, Object>) payloadCaptor.getValue();

        assertEquals("kdoc_previous", payload.get("oldDocumentId"));
        assertEquals(true, payload.get("replaceExisting"));
        @SuppressWarnings("unchecked")
        Map<String, Object> identity = (Map<String, Object>) payload.get("documentIdentity");
        assertEquals("设备甲", identity.get("device_name"));
        assertEquals(List.of("设备乙"), identity.get("aliases"));
        assertEquals(1.0, identity.get("confidence"));
        assertTrue(payload.get("documentId").toString().startsWith("kdoc_"));
    }

    @Test
    void duplicateSuccessCallbackNeverDeletesTheCurrentActiveDocument() {
        KnowledgeDocumentMapper documentMapper = mock(KnowledgeDocumentMapper.class);
        MaintenanceManualMapper manualMapper = mock(MaintenanceManualMapper.class);
        ManualDeviceMapper manualDeviceMapper = mock(ManualDeviceMapper.class);
        MioIOUpLoadService storage = mock(MioIOUpLoadService.class);
        KnowledgeImportProducer producer = mock(KnowledgeImportProducer.class);
        ExpirationService expirationService = mock(ExpirationService.class);
        KnowledgeDocumentServiceImpl service = new KnowledgeDocumentServiceImpl(
                manualMapper,
                storage,
                producer,
                mock(RedissonClient.class),
                expirationService,
                manualDeviceMapper
        );
        ReflectionTestUtils.setField(service, "baseMapper", documentMapper);

        KnowledgeDocument currentDocument = new KnowledgeDocument()
                .setId(101L)
                .setManualId(20L)
                .setDocumentId("kdoc_current")
                .setVersion(3)
                .setStatus("ready")
                .setMinioObjectName("manuals/current.pdf");
        MaintenanceManual manual = new MaintenanceManual()
                .setId(20L)
                .setManualName("设备维修手册")
                .setActiveDocumentId(101L)
                .setStatus(1);

        when(documentMapper.selectOne(any(), anyBoolean())).thenReturn(currentDocument);
        when(documentMapper.selectById(101L)).thenReturn(currentDocument);
        when(manualMapper.selectByIdForUpdate(20L)).thenReturn(manual);
        when(manualDeviceMapper.selectList(any())).thenReturn(List.of());

        TransactionSynchronizationManager.initSynchronization();
        service.onParseSuccess("kdoc_current", Map.of(
                "text_count", 10,
                "image_count", 2,
                "table_count", 1
        ));
        TransactionSynchronizationManager.getSynchronizations()
                .forEach(TransactionSynchronization::afterCommit);

        verify(producer, never()).sendDeleteTask("kdoc_current");
        verify(storage, never()).delete("manuals/current.pdf", BucketEnum.PRIVATE);
    }

    @Test
    void successCallbackLocksManualRowBeforeComparingActiveVersion() {
        KnowledgeDocumentMapper documentMapper = mock(KnowledgeDocumentMapper.class);
        MaintenanceManualMapper manualMapper = mock(MaintenanceManualMapper.class);
        KnowledgeDocumentServiceImpl service = new KnowledgeDocumentServiceImpl(
                manualMapper,
                mock(MioIOUpLoadService.class),
                mock(KnowledgeImportProducer.class),
                mock(RedissonClient.class),
                mock(ExpirationService.class),
                mock(ManualDeviceMapper.class)
        );
        ReflectionTestUtils.setField(service, "baseMapper", documentMapper);

        KnowledgeDocument callbackDocument = new KnowledgeDocument()
                .setId(103L)
                .setManualId(20L)
                .setDocumentId("kdoc_v3")
                .setVersion(3)
                .setStatus("pending");
        MaintenanceManual lockedManual = new MaintenanceManual()
                .setId(20L)
                .setManualName("设备维修手册")
                .setActiveDocumentId(null)
                .setStatus(2);

        when(documentMapper.selectOne(any(), anyBoolean())).thenReturn(callbackDocument);
        when(manualMapper.selectByIdForUpdate(20L)).thenReturn(lockedManual);

        TransactionSynchronizationManager.initSynchronization();
        service.onParseSuccess("kdoc_v3", Map.of());

        verify(manualMapper).selectByIdForUpdate(20L);
        verify(manualMapper, never()).selectById(20L);
    }

    @Test
    void olderCallbackNeverReplacesOrDeletesANewerActiveDocument() {
        KnowledgeDocumentMapper documentMapper = mock(KnowledgeDocumentMapper.class);
        MaintenanceManualMapper manualMapper = mock(MaintenanceManualMapper.class);
        MioIOUpLoadService storage = mock(MioIOUpLoadService.class);
        KnowledgeImportProducer producer = mock(KnowledgeImportProducer.class);
        KnowledgeDocumentServiceImpl service = new KnowledgeDocumentServiceImpl(
                manualMapper,
                storage,
                producer,
                mock(RedissonClient.class),
                mock(ExpirationService.class),
                mock(ManualDeviceMapper.class)
        );
        ReflectionTestUtils.setField(service, "baseMapper", documentMapper);

        KnowledgeDocument callbackDocument = new KnowledgeDocument()
                .setId(102L).setManualId(20L).setDocumentId("kdoc_v2").setVersion(2);
        KnowledgeDocument activeDocument = new KnowledgeDocument()
                .setId(103L).setManualId(20L).setDocumentId("kdoc_v3").setVersion(3)
                .setMinioObjectName("manuals/v3.pdf");
        MaintenanceManual manual = new MaintenanceManual()
                .setId(20L).setActiveDocumentId(103L).setStatus(1);

        when(documentMapper.selectOne(any(), anyBoolean())).thenReturn(callbackDocument);
        when(documentMapper.selectById(103L)).thenReturn(activeDocument);
        when(manualMapper.selectByIdForUpdate(20L)).thenReturn(manual);

        TransactionSynchronizationManager.initSynchronization();
        service.onParseSuccess("kdoc_v2", Map.of());

        verify(manualMapper, never()).updateById(any(MaintenanceManual.class));
        verify(producer, never()).sendDeleteTask(any());
        verify(storage, never()).delete(any(), eq(BucketEnum.PRIVATE));
    }

    @Test
    void newerCallbackActivatesThenDeletesOnlyTheStrictlyOlderResourcesAfterCommit() {
        KnowledgeDocumentMapper documentMapper = mock(KnowledgeDocumentMapper.class);
        MaintenanceManualMapper manualMapper = mock(MaintenanceManualMapper.class);
        MioIOUpLoadService storage = mock(MioIOUpLoadService.class);
        KnowledgeImportProducer producer = mock(KnowledgeImportProducer.class);
        KnowledgeDocumentServiceImpl service = new KnowledgeDocumentServiceImpl(
                manualMapper,
                storage,
                producer,
                mock(RedissonClient.class),
                mock(ExpirationService.class),
                mock(ManualDeviceMapper.class)
        );
        ReflectionTestUtils.setField(service, "baseMapper", documentMapper);

        KnowledgeDocument callbackDocument = new KnowledgeDocument()
                .setId(103L).setManualId(20L).setDocumentId("kdoc_v3").setVersion(3)
                .setFileName("v3.pdf").setFileType(".pdf").setFileSize(1024L)
                .setMinioObjectName("manuals/v3.pdf");
        KnowledgeDocument activeDocument = new KnowledgeDocument()
                .setId(102L).setManualId(20L).setDocumentId("kdoc_v2").setVersion(2)
                .setMinioObjectName("manuals/v2.pdf");
        MaintenanceManual manual = new MaintenanceManual()
                .setId(20L).setManualName("设备维修手册")
                .setActiveDocumentId(102L).setStatus(1);

        when(documentMapper.selectOne(any(), anyBoolean())).thenReturn(callbackDocument);
        when(documentMapper.selectById(102L)).thenReturn(activeDocument);
        when(manualMapper.selectByIdForUpdate(20L)).thenReturn(manual);

        TransactionSynchronizationManager.initSynchronization();
        service.onParseSuccess("kdoc_v3", Map.of());

        assertEquals(103L, manual.getActiveDocumentId());
        verify(manualMapper).updateById(manual);
        verify(producer, never()).sendDeleteTask(any());
        TransactionSynchronizationManager.getSynchronizations()
                .forEach(TransactionSynchronization::afterCommit);
        verify(producer).sendDeleteTask("kdoc_v2");
        verify(storage).delete("manuals/v2.pdf", BucketEnum.PRIVATE);
    }
}
