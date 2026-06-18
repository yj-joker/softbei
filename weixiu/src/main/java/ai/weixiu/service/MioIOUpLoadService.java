package ai.weixiu.service;

import ai.weixiu.enumerate.BucketEnum;
import org.springframework.web.multipart.MultipartFile;

import java.io.InputStream;

public interface MioIOUpLoadService {
    /**
     * 上传文件。
     *
     * <p>公共桶返回永久访问地址，私有桶返回临时预签名地址。</p>
     */
    String upload(MultipartFile file, BucketEnum bucket);

    /** 按对象名从指定桶读取文件流。 */
    InputStream download(String objectName, BucketEnum bucket);

    /** 按对象名删除指定桶中的文件。 */
    void delete(String objectName, BucketEnum bucket);

    /**
     * 上传文件并返回稳定对象名。
     *
     * <p>维修手册会把对象名写入数据库，后续再用它生成新的预签名地址。</p>
     */
    String getObjectName(MultipartFile file, String name);

    /**
     * 为桶内对象生成临时 GET 访问地址。
     *
     * @param objectName MinIO 对象名
     * @param bucket     目标桶
     * @param expiry     过期分钟数
     */
    String getPresignedUrl(String objectName, BucketEnum bucket, int expiry);

}
