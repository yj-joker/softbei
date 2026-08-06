package ai.weixiu.utils;

import ai.weixiu.config.OssConfig;
import cn.hutool.core.lang.UUID;
import com.aliyun.sdk.service.oss2.OSSClient;
import com.aliyun.sdk.service.oss2.models.PutObjectRequest;
import com.aliyun.sdk.service.oss2.models.PutObjectResult;
import com.aliyun.sdk.service.oss2.transport.BinaryData;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.time.LocalDate;
import java.util.Locale;
import ai.weixiu.exception.UploadException;


@Component
public class AliyunUpLoadUtils {

    private final OSSClient ossClient;
    private final OssConfig ossConfig;
    @Value("${aliyun.oss.bucket}")
    private  String bucketName;
    @Value("${aliyun.oss.region}")
    private  String region;

    public AliyunUpLoadUtils(OSSClient ossClient, OssConfig ossConfig) {
        this.ossClient = ossClient;
        this.ossConfig = ossConfig;
    }

    public String upload(MultipartFile file) throws IOException {
        if (file == null || file.isEmpty() || file.getSize() > 10 * 1024 * 1024) {
            throw new UploadException("图片不能为空且不能超过10MB");
        }
        String originalName = file.getOriginalFilename();
        String suffix = originalName != null && originalName.contains(".")
                ? originalName.substring(originalName.lastIndexOf(".")).toLowerCase(Locale.ROOT)
                : "";
        if (!java.util.Set.of(".jpg", ".jpeg", ".png", ".gif", ".webp").contains(suffix)
                || file.getContentType() == null || !file.getContentType().toLowerCase(Locale.ROOT).startsWith("image/")) {
            throw new UploadException("仅支持 jpg、png、gif、webp 图片");
        }

        String key = "uploads/" + LocalDate.now() + "/" + UUID.randomUUID() + suffix;

        PutObjectResult result = ossClient.putObject(PutObjectRequest.newBuilder()
                .bucket(ossConfig.getBucket())
                .key(key)
                .body(BinaryData.fromBytes(file.getBytes()))
                .contentType(file.getContentType())
                .build());

        return "https://" + bucketName + ".oss-" + region + ".aliyuncs.com/" + key;
    }
}

