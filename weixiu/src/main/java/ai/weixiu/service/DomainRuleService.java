package ai.weixiu.service;

import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.DomainRuleDTO;
import ai.weixiu.pojo.vo.DomainRuleVO;

public interface DomainRuleService {

    DomainRuleVO create(DomainRuleDTO dto);

    DomainRuleVO update(Long id, DomainRuleDTO dto);

    void submit(Long id);

    void approve(Long id, DomainRuleDTO dto);

    void reject(Long id, String comment);

    void disable(Long id);

    void retrySync(Long id);

    PageResult<DomainRuleVO> page(int page, int size, String status, String keyword, String deviceType);

    DomainRuleVO detail(Long id);
}
