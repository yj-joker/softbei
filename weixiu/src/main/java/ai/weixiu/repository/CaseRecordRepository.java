package ai.weixiu.repository;

import ai.weixiu.entity.CaseRecord;
import org.springframework.data.neo4j.repository.Neo4jRepository;
import org.springframework.data.neo4j.repository.query.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface CaseRecordRepository extends Neo4jRepository<CaseRecord, String> {

    @Query("MATCH (c:CaseRecord) WHERE c.status = $status " +
            "RETURN c ORDER BY c.recorded_at DESC SKIP $skip LIMIT $size")
    List<CaseRecord> findByStatus(@Param("status") String status,
                                  @Param("skip") long skip,
                                  @Param("size") int size);

    @Query("MATCH (c:CaseRecord) WHERE c.status = $status RETURN count(c)")
    long countByStatus(@Param("status") String status);

    @Query("MATCH (c:CaseRecord) WHERE c.submitted_by_id = $uid " +
            "RETURN c ORDER BY c.recorded_at DESC SKIP $skip LIMIT $size")
    List<CaseRecord> findBySubmittedBy(@Param("uid") Long uid,
                                       @Param("skip") long skip,
                                       @Param("size") int size);

    @Query("MATCH (c:CaseRecord) WHERE c.submitted_by_id = $uid RETURN count(c)")
    long countBySubmittedBy(@Param("uid") Long uid);

    /**
     * 某用户已审核通过(approved)的案例履历，按记录时间倒序，供用户画像生成使用。
     */
    @Query("MATCH (c:CaseRecord) WHERE c.submitted_by_id = $uid AND c.status = 'approved' " +
            "RETURN c ORDER BY c.recorded_at DESC LIMIT $limit")
    List<CaseRecord> findApprovedBySubmittedBy(@Param("uid") Long uid, @Param("limit") int limit);

    /**
     * 多模态向量召回 approved 案例（RAG 出口用）。
     */
    @Query("""
            CALL db.index.vector.queryNodes('case_record_multimodal_index', $limit, $embedding)
            YIELD node, score
            WHERE node.status = 'approved' AND score >= $minScore
            RETURN node.id AS id,
                   node.title AS title,
                   node.summary AS summary,
                   node.diagnosis AS diagnosis,
                   node.resolution AS resolution,
                   node.result AS result,
                   node.experience_summary AS experienceSummary,
                   node.tags AS tags,
                   node.image_urls AS imageUrls,
                   node.fault_name AS faultName,
                   node.source_task_id AS sourceTaskId,
                   score
            ORDER BY score DESC
            """)
    List<ai.weixiu.pojo.vo.CaseRecordVO> getCasesByMultimodalEmbedding(
            @Param("embedding") List<Double> embedding,
            @Param("limit") long limit,
            @Param("minScore") double minScore);

    /**
     * 某故障下的 approved 案例分页（图谱展开用）。
     */
    @Query("""
            MATCH (cr:CaseRecord)-[:RECORDED]->(f:Fault {id: $faultId})
            WHERE cr.status = 'approved'
            RETURN cr.id AS id,
                   cr.title AS title,
                   cr.summary AS summary,
                   cr.diagnosis AS diagnosis,
                   cr.resolution AS resolution,
                   cr.result AS result,
                   cr.experience_summary AS experienceSummary,
                   cr.tags AS tags,
                   cr.image_urls AS imageUrls,
                   cr.recorded_at AS recordedAt
            ORDER BY cr.recorded_at DESC SKIP $skip LIMIT $size
            """)
    List<ai.weixiu.pojo.vo.CaseRecordVO> findApprovedByFault(@Param("faultId") String faultId,
                                                             @Param("skip") long skip,
                                                             @Param("size") int size);

    @Query("MATCH (cr:CaseRecord)-[:RECORDED]->(f:Fault {id: $faultId}) " +
            "WHERE cr.status = 'approved' RETURN count(cr)")
    long countApprovedByFault(@Param("faultId") String faultId);
}
