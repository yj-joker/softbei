package ai.weixiu.utils;

import ai.weixiu.exceprion.NullException;
import com.alibaba.excel.EasyExcel;
import com.alibaba.excel.context.AnalysisContext;
import com.alibaba.excel.read.listener.ReadListener;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

@Slf4j
@Component
public class ExcelUtils<T> {

    /**
     * 校验文件是否为 Excel 文件（根据 MIME 类型判断）
     */
    public static boolean isExcelFile(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            log.info("文件为空");
            return false;
        }

        String filename = file.getOriginalFilename();
        String contentType = file.getContentType();
        log.info("上传文件校验: filename={}, contentType={}", filename, contentType);

        if (filename == null || filename.isBlank()) {
            log.info("文件名为空");
            return false;
        }

        String lowerName = filename.toLowerCase();
        boolean validSuffix = lowerName.endsWith(".xlsx") || lowerName.endsWith(".xls");
        if (!validSuffix) {
            log.info("文件后缀不是Excel: {}", filename);
            return false;
        }

        return true;
    }

    /*
    * 读取excel里对应数据的工具
    * */
    public static <T> List<T> readExcel(MultipartFile file, Class<T> clazz) {
        return readExcel(file, clazz, 0);
    }

    public static <T> List<T> readExcel(MultipartFile file, Class<T> clazz, int sheetNo) {
        List<T> list = new ArrayList<>();
        try {
            EasyExcel.read(file.getInputStream(), clazz, new ReadListener<T>() {
                @Override
                public void invoke(T data, AnalysisContext context) {
                    list.add(data);
                }
                @Override
                public void doAfterAllAnalysed(AnalysisContext context) {
                    log.info("Excel读取完成，共{}条数据", list.size());
                }
            }).sheet(sheetNo).headRowNumber(1).doReadSync();
        } catch (IOException e) {
            log.error("读取Excel文件失败", e);
        }
        return list;
    }
}
