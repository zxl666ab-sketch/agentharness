package com.caijiatai.procurement.report;

import java.util.List;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface AuditEventRepository extends JpaRepository<AuditEvent, String> {
    List<AuditEvent> findByTaskIdOrderByCreatedAtAscIdAsc(String taskId);
    java.util.List<AuditEvent> findAllByOrderByCreatedAtDescIdDesc(org.springframework.data.domain.Pageable pageable);

    /** 超时调度幂等去重：同一任务同一事件类型只写一次（一任务一订单/一对账单）。 */
    boolean existsByTaskIdAndEventType(String taskId, String eventType);

    long countByEventType(String eventType);

    /** K6 全局审计筛选（类型/操作人/业务对象/任务）。 */
    @Query("""
            select event from AuditEvent event
            where (:type is null or :type = '' or event.eventType = :type)
              and (:actor is null or :actor = '' or event.actor = :actor)
              and (:businessType is null or :businessType = '' or event.businessType = :businessType)
              and (:taskId is null or :taskId = '' or event.taskId = :taskId)
            """)
    Page<AuditEvent> search(
            @Param("type") String type,
            @Param("actor") String actor,
            @Param("businessType") String businessType,
            @Param("taskId") String taskId,
            Pageable pageable);
}
