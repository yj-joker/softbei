package ai.weixiu.pojo;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class PageResult <T> {
    private List<T> records;
    private Long total;
    private Integer page;
    private Integer size;
}
