package ai.weixiu.pojo.query;

import lombok.Data;

@Data
public class PageQuery {
    private Integer page;//第几页
    private Integer size;//每页多少条
    private String sortBy;//排序字段
    private Integer isAsc;//是否升序  1:升序 0:降序
}
