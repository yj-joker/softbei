package ai.weixiu.repository;

import ai.weixiu.entity.Component;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.FaultVO;
import org.springframework.data.neo4j.repository.Neo4jRepository;
import org.springframework.data.neo4j.repository.query.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface ComponentRepository extends Neo4jRepository<Component, String> {

    /*
     * 分页查询部件故障 - 数据列表
     * */
    @Query("""
            MATCH (c:Component {id: $componentId})-[:CAUSES]->(f:Fault)
            WHERE $faultName IS NULL OR $faultName = '' OR f.name CONTAINS $faultName
            RETURN f.id AS id,
                   f.code AS code,
                   f.name AS name,
                   f.description AS description,
                   f.severity AS severity,
                   f.category AS category,
                   f.occurrence_time AS occurrenceTime,
                   f.reported_by AS reportedBy
            ORDER BY f.name
            SKIP $skip
            LIMIT $limit
            """)
    List<FaultVO> getFaultRecords(
            @Param("componentId") String componentId,
            @Param("faultName") String faultName,
            @Param("skip") int skip,
            @Param("limit") int limit
    );

    /*
     * 分页查询部件故障 - 总数
     * */
    @Query("""
            MATCH (c:Component {id: $componentId})-[:CAUSES]->(f:Fault)
            WHERE $faultName IS NULL OR $faultName = '' OR f.name CONTAINS $faultName
            RETURN count(f) AS total
            """)
    Long getFaultTotal(
            @Param("componentId") String componentId,
            @Param("faultName") String faultName
    );

    @Query("""
            CALL db.index.vector.queryNodes('component_embedding_index', $limit, $embedding)
            YIELD node AS c, score
            WHERE score >= $minScore
            RETURN c.id AS id,
                   c.name AS name,
                   c.part_number AS partNumber,
                   c.specification AS specification,
                   c.supplier AS supplier,
                   c.lifecycle AS lifecycle,
                   c.unit_price AS unitPrice,
                   score
            ORDER BY score DESC
            """)
    List<ComponentVO> getComponentByEmbedding(List<Double> embedding, Long limit, Double minScore);

    /**
     * 通过多模态融合向量检索最相似的部件
     */
    @Query("""
        CALL db.index.vector.queryNodes('component_multimodal_index', $limit, $embedding)
        YIELD node AS c, score
        WHERE score >= $minScore
        RETURN c.id AS id,
               c.name AS name,
               c.part_number AS partNumber,
               c.specification AS specification,
               c.supplier AS supplier,
               c.lifecycle AS lifecycle,
               c.unit_price AS unitPrice,
               c.image_urls AS imageUrls,
               score
        ORDER BY score DESC
        """)
    List<ComponentVO> getComponentByMultimodalEmbedding(
        @Param("embedding") List<Double> embedding,
        @Param("limit") Long limit,
        @Param("minScore") Double minScore
    );
}
