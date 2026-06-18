package ai.weixiu.repository;

import ai.weixiu.entity.Fault;
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.pojo.vo.SolutionVO;
import org.springframework.data.neo4j.repository.Neo4jRepository;
import org.springframework.data.neo4j.repository.query.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface FaultRepository extends Neo4jRepository<Fault, String> {

    /*
    * 分页查询故障解决方案 - 数据列表
    * */
    @Query("""
        MATCH (f:Fault {id: $faultId})-[:HAS_SOLUTION]->(s:Solution)
        WHERE $solutionTitle IS NULL OR $solutionTitle = '' OR s.title CONTAINS $solutionTitle
        RETURN s.id AS id,
               s.code AS code,
               s.title AS title,
               s.description AS description,
               s.tools_required AS toolsRequired,
               s.estimated_time AS estimatedTime,
               s.difficulty AS difficulty,
               s.created_at AS createdAt,
               s.verified AS verified
        ORDER BY s.title
        SKIP $skip
        LIMIT $limit
        """)
    List<SolutionVO> getSolutionRecords(
        @Param("faultId") String faultId,
        @Param("solutionTitle") String solutionTitle,
        @Param("skip") int skip,
        @Param("limit") int limit
    );

    /*
    * 分页查询故障解决方案 - 总数
    * */
    @Query("""
        MATCH (f:Fault {id: $faultId})-[:HAS_SOLUTION]->(s:Solution)
        WHERE $solutionTitle IS NULL OR $solutionTitle = '' OR s.title CONTAINS $solutionTitle
        RETURN count(s) AS total
        """)
    Long getSolutionTotal(
        @Param("faultId") String faultId,
        @Param("solutionTitle") String solutionTitle
    );
    /*
    * 根据用户描述向量返回最接近的故障
    * */
    @Query(""" 
CALL db.index.vector.queryNodes('fault_embedding_index', $limit, $embedding)
YIELD node AS f, score
WHERE score >= $minScore
RETURN f.id AS id,
       f.name AS name,
       f.description AS description,
       f.category AS category,
       f.severity AS severity,
       score
ORDER BY score DESC
            """)
    List<FaultVO> getFaultsByEmbedding(
        @Param("embedding") List<Double> embedding,
        @Param("limit") long limit, // 返回数量
        @Param("minScore") double minScore // 最小分数
    );

    /**
     * 通过多模态融合向量检索最相似的故障
     */
    @Query("""
        CALL db.index.vector.queryNodes('fault_multimodal_index', $limit, $embedding)
        YIELD node AS f, score
        WHERE score >= $minScore
        RETURN f.id AS id,
               f.name AS name,
               f.description AS description,
               f.category AS category,
               f.severity AS severity,
               f.image_urls AS imageUrls,
               score
        ORDER BY score DESC
        """)
    List<FaultVO> getFaultsByMultimodalEmbedding(
        @Param("embedding") List<Double> embedding,
        @Param("limit") long limit,
        @Param("minScore") double minScore
    );

}
