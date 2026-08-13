package com.caijiatai.procurement.report;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AuditEventRepository extends JpaRepository<AuditEvent, String> {
    List<AuditEvent> findByTaskIdOrderByCreatedAtAscIdAsc(String taskId);
    java.util.List<AuditEvent> findAllByOrderByCreatedAtDescIdDesc(org.springframework.data.domain.Pageable pageable);

    /** 超时调度幂等去重：同一任务同一事件类型只写一次（一任务一订单/一对账单）。 */
    boolean existsByTaskIdAndEventType(String taskId, String eventType);
}
