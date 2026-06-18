package ai.weixiu.repository;

import ai.weixiu.entity.Device;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.DeviceOverviewVO;
import ai.weixiu.pojo.vo.DeviceVO;
import org.springframework.data.neo4j.repository.Neo4jRepository;
import org.springframework.data.neo4j.repository.query.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface DeviceRepository extends Neo4jRepository<Device, String> {

    /*
     * 获取设备概览信息
     * */
    @Query("""
            MATCH (d:Device {id: $deviceId})
            OPTIONAL MATCH (d)-[:OWNS]->(c:Component)
            WITH d, count(DISTINCT c) AS componentCount
            OPTIONAL MATCH (d)-[:HAS_FAULT]->(f:Fault)
            RETURN d.id AS deviceId,
                   d.name AS deviceName,
                   d.code AS code,
                   d.model AS model,
                   d.location AS location,
                   d.manufacturer AS manufacturer,
                   componentCount,
                   count(DISTINCT f) AS faultCount
            """)
    Optional<DeviceOverviewVO> getDeviceOverview(@Param("deviceId") String deviceId);

    /*
     * 分页获取部件 - 数据列表
     * */
    @Query("""
            MATCH (d:Device {id: $deviceId})-[:OWNS]->(c:Component)
            WHERE $componentName IS NULL OR $componentName = '' OR c.name CONTAINS $componentName
            RETURN c.id AS id,
                   c.name AS name,
                   c.part_number AS partNumber,
                   c.specification AS specification,
                   c.supplier AS supplier,
                   c.lifecycle AS lifecycle,
                   c.unit_price AS unitPrice
            ORDER BY c.name
            SKIP $skip
            LIMIT $limit
            """)
    List<ComponentVO> getComponentRecords(
            @Param("deviceId") String deviceId,
            @Param("componentName") String componentName,
            @Param("skip") int skip,
            @Param("limit") int limit
    );

    /*
     * 分页获取部件 - 总数
     * */
    @Query("""
            MATCH (d:Device {id: $deviceId})-[:OWNS]->(c:Component)
            WHERE $componentName IS NULL OR $componentName = '' OR c.name CONTAINS $componentName
            RETURN count(c) AS total
            """)
    Long getComponentTotal(
            @Param("deviceId") String deviceId,
            @Param("componentName") String componentName
    );
    /*
     * 获取对应条件的设备
     * */
    @Query("""
             MATCH (d:Device)
                         WHERE $keyword IS NULL
                            OR $keyword = ''
                            OR d.name CONTAINS $keyword
                            OR d.code CONTAINS $keyword
                            OR d.model CONTAINS $keyword
                            OR d.location CONTAINS $keyword
                         RETURN d.id AS id,
                                d.name AS name,
                                d.code AS code,
                                d.model AS model,
                                d.location AS location,
                                d.manufacturer AS manufacturer
                         ORDER BY d.name ASC
                         SKIP $skip
                         LIMIT $limit
            """)
    List<DeviceVO> getDevices(
            @Param("keyword") String keyword,
            @Param("skip") int skip,
            @Param("limit") int limit
    );
    /*
     * 获取对应条件的设备总数
     * */
    @Query("""
              MATCH (d:Device)
                         WHERE $keyword IS NULL
                            OR $keyword = ''
                            OR d.name CONTAINS $keyword
                            OR d.code CONTAINS $keyword
                            OR d.model CONTAINS $keyword
                            OR d.location CONTAINS $keyword
                         RETURN  count(d) AS total
            """)
    Long getDeviceTotal(@Param("keyword") String keyword);

    /**
     * 根据位置查询该位置下所有设备的 ID 列表。
     * 用于个性化推荐：同场地设备 → 手册扩展推荐。
     */
    @Query("""
            MATCH (d:Device)
            WHERE d.location = $location
            RETURN d.id AS id
            """)
    List<String> findDeviceIdsByLocation(@Param("location") String location);

    /**
     * 根据设备 ID 列表批量查询设备的 location。
     */
    @Query("""
            MATCH (d:Device)
            WHERE d.id IN $deviceIds
            RETURN d.id AS id, d.location AS location
            """)
    List<DeviceLocationProjection> findLocationsByDeviceIds(@Param("deviceIds") List<String> deviceIds);

    interface DeviceLocationProjection {
        String getId();
        String getLocation();
    }
}