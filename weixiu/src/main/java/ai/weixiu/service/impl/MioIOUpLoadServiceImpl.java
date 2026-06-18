package ai.weixiu.service.impl;

import ai.weixiu.config.MinioProperties;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.exceprion.UploadException;
import ai.weixiu.service.MioIOUpLoadService;
import io.minio.*;
import io.minio.http.Method;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NonNull;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

@Service
@RequiredArgsConstructor
@Slf4j
/**
 * MinIO 文件存储实现。
 *
 * <p>公共桶文件可以返回固定访问地址；私有桶文件只能返回带过期时间的预签名地址。
 * 维修手册文档走私有桶，因此业务层需要保存对象名，并在用户真正访问详情时再生成临时 URL。</p>
 */
public class MioIOUpLoadServiceImpl implements MioIOUpLoadService {
    /** MinIO 官方客户端，负责对象和 Bucket 操作。 */
    private final MinioClient minioClient;

    /** MinIO 服务端点等项目配置，用于拼装公共桶访问地址。 */
    private final MinioProperties minioProperties;

    /**
     * 项目启动时检查所有业务桶。
     *
     * <p>若枚举声明的桶还不存在，则在启动阶段创建，避免第一次上传时才暴露桶缺失问题。</p>
     */
    @PostConstruct
    public void init() {
        for (BucketEnum bucket : BucketEnum.values()) {
            ensureBucketExists(bucket.getName());
        }
    }


    /** 单文件上传大小上限：50MB */
    private static final long MAX_UPLOAD_SIZE = 50 * 1024 * 1024;

    /**
     * 上传文件并返回可访问结果。
     *
     * <p>这是通用上传入口：公共桶返回永久 URL，私有桶返回预签名 URL。
     * 若业务需要把文件和数据库记录长期绑定，应优先使用 {@link #getObjectName(MultipartFile, String)}
     * 保存对象名，而不是把私有桶预签名地址直接存库。</p>
     */
    @Override
    public String upload(MultipartFile file, BucketEnum bucket) {
        checkFileSize(file);
        // 生成唯一文件名，避免覆盖
        return switch (bucket) {
            case PUBLIC -> uploadPublicFile(file,bucket.getName());
            case PRIVATE -> uploadPrivateFile(file,bucket.getName());
        };
    }

    /**
     * 校验文件大小是否超出限制（50MB）。
     */
    private void checkFileSize(MultipartFile file) {
        if (file.getSize() > MAX_UPLOAD_SIZE) {
            throw new UploadException(
                    String.format("文件大小 %.2fMB 超出限制（最大 %dMB）",
                            file.getSize() / (1024.0 * 1024.0),
                            MAX_UPLOAD_SIZE / (1024 * 1024))
            );
        }
    }

    @Override
    /** 按对象名下载指定桶中的文件流，调用方负责后续流消费。 */
    public InputStream download(String objectName, BucketEnum bucket) {
        try {
            return minioClient.getObject(
                    GetObjectArgs.builder()
                            .bucket(bucket.getName())
                            .object(objectName)
                            .build()
            );
        } catch (Exception e) {
            log.error("文件下载失败: {}", e.getMessage());
            throw new RuntimeException("文件下载失败", e);
        }
    }

    @Override
    /** 删除指定桶中的对象。 */
    public void delete(String objectName, BucketEnum bucket) {
        try {
            minioClient.removeObject(
                    RemoveObjectArgs.builder()
                            .bucket(bucket.getName())
                            .object(objectName)
                            .build()
            );
            log.info("文件删除成功: {}", objectName);
        } catch (Exception e) {
            log.error("文件删除失败: {}", e.getMessage());
            throw new RuntimeException("文件删除失败", e);
        }
    }

    @Override
    /**
     * 上传文件并返回 MinIO 对象名。
     *
     * <p>对象名使用 UUID 和原始后缀构造，避免同名文件覆盖。
     * 维修手册把这个值写入 minioObjectName 字段，后续详情访问、文件替换和删除都依赖它。</p>
     */
    public @NonNull String getObjectName(MultipartFile file, String name) {
        checkFileSize(file);
        try {
            // 生成唯一文件名，避免覆盖
            String originalFilename = file.getOriginalFilename();
            String ext = "";
            if (originalFilename != null && originalFilename.contains(".")) {
                ext = originalFilename.substring(originalFilename.lastIndexOf("."));
            }
            String objectName = UUID.randomUUID().toString().replace("-", "") + ext;

            minioClient.putObject(
                    PutObjectArgs.builder()
                            .bucket(name)
                            .object(objectName)
                            .stream(file.getInputStream(), file.getSize(), -1)
                            .contentType(file.getContentType())
                            .build()
            );
            log.info("文件上传成功: {}", objectName);
            return objectName;
        } catch (Exception e) {
            log.error("文件上传失败: {}", e.getMessage());
            throw new UploadException("文件上传失败");
        }
    }

    /** 上传到私有桶后返回临时访问地址。 */
    private String uploadPrivateFile(MultipartFile file, String name) {
        String objectName = getObjectName(file, name);
        return getPresignedUrl(objectName, BucketEnum.PRIVATE,120);
    }

    /** 上传到公共桶后返回固定访问地址。 */
    private String uploadPublicFile(MultipartFile file, String name) {
        String objectName = getObjectName(file, name);
        return getFileUrl(objectName, name);
    }


    /**
     * 获取公共桶文件的永久访问地址。
     *
     * <p>该地址依赖 Bucket 可公开访问，不适用于维修手册私有桶文件。</p>
     */
    public String getFileUrl(String objectName, String bucketName) {
        return minioProperties.getEndpoint() + "/"
                + bucketName + "/" + objectName;
    }
    /**
     * 获取文件的预签名访问 URL。
     *
     * @param objectName MinIO 对象名
     * @param bucket     对象所在桶
     * @param expiry     过期时间，单位为分钟
     */
    @Override
    public String getPresignedUrl(String objectName, BucketEnum bucket, int expiry) {
        // 私有桶对象不能暴露永久公开地址。
        // 调用方传入稳定的对象名，这里返回可供浏览器临时访问的 GET 预签名 URL。
        return getPresignedUrl(objectName, bucket.getName(), expiry);
    }

    /** 调用 MinIO 客户端生成 GET 预签名地址。PDF 文件强制浏览器内联预览而非下载。 */
    private String getPresignedUrl(String objectName,String bucketName,int expiry) {
        try {
            GetPresignedObjectUrlArgs.Builder builder = GetPresignedObjectUrlArgs.builder()
                    .method(Method.GET)
                    .bucket(bucketName)
                    .object(objectName)
                    .expiry(expiry, TimeUnit.MINUTES);

            // PDF 文件：覆盖响应头，让浏览器内联显示而非下载，#page=N 锚点才能生效
            if (objectName.toLowerCase().endsWith(".pdf")) {
                Map<String, String> queryParams = new HashMap<>();
                queryParams.put("response-content-type", "application/pdf");
                queryParams.put("response-content-disposition", "inline");
                builder.extraQueryParams(queryParams);
            }

            return minioClient.getPresignedObjectUrl(builder.build());
        } catch (Exception e) {
            log.error("获取预签名 URL 失败: {}", e.getMessage());
            throw new RuntimeException("获取预签名 URL 失败", e);
        }
    }
    /**
     * 确保指定 Bucket 已存在。
     *
     * <p>当前方法在启动阶段调用，发现桶不存在时立即创建；创建失败则阻止服务继续以半可用状态运行。</p>
     */
    private void ensureBucketExists(String bucketName) {
        try {
            boolean exists = minioClient.bucketExists(
                    BucketExistsArgs.builder().bucket(bucketName).build()
            );
            if (!exists) {
                minioClient.makeBucket(
                        MakeBucketArgs.builder().bucket(bucketName).build()
                );
                log.info("Bucket [{}] 创建成功", bucketName);
            }
        } catch (Exception e) {
            log.error("创建 Bucket [{}] 失败: {}", bucketName, e.getMessage());
            throw new RuntimeException("MinIO 初始化失败", e);
        }
    }
}
