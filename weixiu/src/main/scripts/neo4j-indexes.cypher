// =============================================================
// Neo4j 知识图谱索引初始化脚本
// 项目：设备检修知识图谱（weixiu）
// 更新日期：2026-05-20
//
// 图谱结构：
//   Device -[OWNS]-> Component -[CAUSES]-> Fault -[HAS_SOLUTION]-> Solution
//   Device -[HAS_FAULT]-> Fault（历史故障记录）
//
// 使用方式：在 Neo4j Browser 或 cypher-shell 中逐条执行
// =============================================================


// ======================== 一、向量索引（必须，共 4 个）========================
//
// 文本向量：text-embedding-v4 模型，1536 维，cosine 相似度
// 多模态向量：qwen2.5-vl-embedding 模型，1024 维，cosine 相似度
//
// 仅 Fault 和 Component 需要向量索引：
//   - Device：通过关键字模糊匹配检索，不需要向量
//   - Solution：通过关系 HAS_SOLUTION 从 Fault 遍历到达，不需要向量

// 1. Fault 文本向量索引（1536 维）
//    用途：用户输入故障描述文字 → 文本向量化 → 搜索最相似的故障节点
//    属性：Fault.embedding
CALL db.index.vector.createNodeIndex(
  'fault_embedding_index', 'Fault', 'embedding', 1536, 'cosine'
);

// 2. Component 文本向量索引（1536 维）
//    用途：用户输入部件描述文字 → 文本向量化 → 搜索最相似的部件节点
//    属性：Component.embedding
CALL db.index.vector.createNodeIndex(
  'component_embedding_index', 'Component', 'embedding', 1536, 'cosine'
);

// 3. Fault 多模态向量索引（1024 维）
//    用途：用户上传故障图片 → 图片向量化 → 搜索最相似的故障节点
//    属性：Fault.multimodal_embedding
CALL db.index.vector.createNodeIndex(
  'fault_multimodal_index', 'Fault', 'multimodal_embedding', 1024, 'cosine'
);

// 4. Component 多模态向量索引（1024 维）
//    用途：用户上传部件图片 → 图片向量化 → 搜索最相似的部件节点
//    属性：Component.multimodal_embedding
CALL db.index.vector.createNodeIndex(
  'component_multimodal_index', 'Component', 'multimodal_embedding', 1024, 'cosine'
);


// ======================== 二、TEXT 索引（建议，共 7 个）========================
//
// 加速 CONTAINS 模糊查询，数据量增长后收益明显
// 要求 Neo4j 4.4+ 版本

// 5. Device 名称
CREATE TEXT INDEX device_name_text_index IF NOT EXISTS FOR (d:Device) ON (d.name);

// 6. Device 编码
CREATE TEXT INDEX device_code_text_index IF NOT EXISTS FOR (d:Device) ON (d.code);

// 7. Device 型号
CREATE TEXT INDEX device_model_text_index IF NOT EXISTS FOR (d:Device) ON (d.model);

// 8. Device 位置
CREATE TEXT INDEX device_location_text_index IF NOT EXISTS FOR (d:Device) ON (d.location);

// 9. Component 名称
CREATE TEXT INDEX component_name_text_index IF NOT EXISTS FOR (c:Component) ON (c.name);

// 10. Fault 名称
CREATE TEXT INDEX fault_name_text_index IF NOT EXISTS FOR (f:Fault) ON (f.name);

// 11. Solution 标题
CREATE TEXT INDEX solution_title_text_index IF NOT EXISTS FOR (s:Solution) ON (s.title);


// ======================== 三、验证索引 ========================
// 执行以下命令查看所有已创建的索引：
SHOW INDEXES;
