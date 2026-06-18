package ai.weixiu.repository;

import ai.weixiu.entity.Solution;
import ai.weixiu.pojo.vo.SolutionVO;
import org.springframework.data.neo4j.repository.Neo4jRepository;
import org.springframework.data.neo4j.repository.query.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface SolutionRepository extends Neo4jRepository<Solution, String> {

    /**
     * 分页查询解决方案列表 - 数据
     */
    @Query("""
        MATCH (s:Solution)
        WHERE ($title IS NULL OR $title = '' OR s.title CONTAINS $title)
          AND ($difficulty IS NULL OR $difficulty = '' OR s.difficulty = $difficulty)
          AND ($verified IS NULL OR s.verified = $verified)
        RETURN s.id AS id,
               s.code AS code,
               s.title AS title,
               s.description AS description,
               s.tools_required AS toolsRequired,
               s.estimated_time AS estimatedTime,
               s.difficulty AS difficulty,
               s.created_at AS createdAt,
               s.verified AS verified,
               s.image_urls AS imageUrls
        ORDER BY s.created_at DESC
        SKIP $skip
        LIMIT $limit
        """)
    List<SolutionVO> findSolutionPage(
        @Param("title") String title,
        @Param("difficulty") String difficulty,
        @Param("verified") Boolean verified,
        @Param("skip") int skip,
        @Param("limit") int limit
    );

    /**
     * 分页查询解决方案列表 - 总数
     */
    @Query("""
        MATCH (s:Solution)
        WHERE ($title IS NULL OR $title = '' OR s.title CONTAINS $title)
          AND ($difficulty IS NULL OR $difficulty = '' OR s.difficulty = $difficulty)
          AND ($verified IS NULL OR s.verified = $verified)
        RETURN count(s) AS total
        """)
    Long countSolutionPage(
        @Param("title") String title,
        @Param("difficulty") String difficulty,
        @Param("verified") Boolean verified
    );
}
