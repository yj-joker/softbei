"""
图谱查询工具（通过 Java 端 HTTP 接口）

调用 Java 后端 /weixiu/path/search 接口查询诊断路径，
复用 Java 端完善的统一查询逻辑（OR 召回 + matchScore 排序 + 向量检索）。

【设计原则】
Python Agent 不直连 Neo4j 查路径，统一走 Java 端：
- Java 端负责：设备模糊匹配、文本向量检索、图片向量检索、OR Cypher、matchScore 排序
- Python 端负责：LLM 意图拆分、调用工具、格式化结果给 LLM

【调用链】
FixAgent ReAct → GraphJavaTool._execute()
    → HTTP POST Java /weixiu/path/search
    → Java GraphQueryServiceImpl.searchDiagnosisPaths()
    → 返回 DiagnosisPathVO 列表
    → 格式化为文本 → 返回给 LLM

【关联】
- 上游：agents/fix_agent.py（ReAct 循环中调用）
- 下游：Java PathController /weixiu/path/search
- 继承：tools/base_tool.py 的 BaseTool
"""

import logging
from typing import Optional, List

import httpx

from tools.base_tool import BaseTool, ToolException
from config.settings import get_settings

logger = logging.getLogger(__name__)


class JavaGraphDiagnosisPathTool(BaseTool):
    """
    通过 Java 后端查询图谱诊断路径

    支持三种输入维度（任意组合，OR 召回）：
    - keyword: 设备名称关键字 → 模糊匹配设备
    - fault_description: 故障描述 → 文本向量搜 fault 索引
    - component_description: 部件描述 → 文本向量搜 component 索引
    - image_urls: 故障图片 → 图片向量搜多模态索引

    返回格式化的图谱证据链文本，LLM 可直接阅读。
    """

    def __init__(self):
        self._settings = get_settings()
        self._base_url = self._settings.java_service_url

    @property
    def name(self) -> str:
        return "java_graph_diagnosis_path"

    @property
    def description(self) -> str:
        return (
            "从设备检修知识图谱中查询诊断路径（调用 Java 后端统一接口）。"
            "支持设备关键字、故障描述、部件描述、故障图片四种输入维度，任意组合。"
            "返回完整的诊断链路：设备→部件→故障→解决方案，按匹配度排序。"
            "适用场景：用户描述故障现象或上传故障图片时，查找可能的故障原因和维修方案。"
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "设备名称关键字，模糊匹配设备名称/编码/型号/位置"
                },
                "fault_description": {
                    "type": "string",
                    "description": "故障现象描述，用于语义匹配故障节点。从用户描述中提取故障相关内容"
                },
                "component_description": {
                    "type": "string",
                    "description": "部件描述，用于语义匹配部件节点。从用户描述中提取部件相关内容"
                },
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "故障图片 URL 列表（MinIO 地址），用于图片向量检索"
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量上限，默认10",
                    "default": 10
                }
            }
        }

    async def _execute(
        self,
        keyword: str = None,
        fault_description: str = None,
        component_description: str = None,
        image_urls: list = None,
        limit: int = 10
    ) -> dict:
        has_keyword = bool(keyword and keyword.strip())
        has_fault = bool(fault_description and fault_description.strip())
        has_comp = bool(component_description and component_description.strip())
        has_images = bool(image_urls)

        if not has_keyword and not has_fault and not has_comp and not has_images:
            return {"paths": [], "message": "至少需要提供设备关键字、故障描述、部件描述或图片之一"}

        try:
            # 构建 DiagnosisSearchQuery 请求体（与 Java 端字段对齐）
            body = {
                "page": 0,
                "size": limit,
                "minScore": 0.70
            }
            if has_keyword:
                body["keyword"] = keyword.strip()
            if has_fault:
                body["faultDescription"] = fault_description.strip()
            if has_comp:
                body["componentDescription"] = component_description.strip()
            if has_images:
                body["imageUrls"] = image_urls

            logger.info("[graph_java_tool] 调用 Java 图谱查询: keyword=%s, "
                        "fault_desc=%s, comp_desc=%s, images=%d",
                        keyword, fault_description, component_description, len(image_urls or []))

            headers = {"X-Internal-Token": self._settings.internal_token}

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/weixiu/path/search",
                    json=body,
                    headers=headers
                )
                resp.raise_for_status()
                result = resp.json()

            data = result.get("data", {})
            records = data.get("records", [])
            total = data.get("total", 0)
            cases = data.get("cases", []) or []

            if not records and not cases:
                logger.info("[graph_java_tool] 未找到匹配的诊断路径或案例")
                return {
                    "paths_found": 0,
                    "cases_found": 0,
                    "context": "【图谱查询结果】\n未找到匹配的诊断路径。\n"
                }

            for i, r in enumerate(records):
                logger.info("[graph_java_tool] 路径%d: %s → %s → %s (matchScore=%s, 方案数=%d)",
                            i + 1,
                            r.get("deviceName", "?"),
                            r.get("componentName", "?"),
                            r.get("faultName", "?"),
                            r.get("matchScore", "?"),
                            len(r.get("solutions") or []))

            logger.info("[graph_java_tool] 查询完成: 命中 %d 条路径, 相关案例 %d 条, 总计 %d",
                        len(records), len(cases), total)

            context = self._format_paths(records, keyword)
            if cases:
                context = context + "\n" + self._format_cases(cases)

            return {
                "paths_found": len(records),
                "cases_found": len(cases),
                "total": total,
                "context": context,
                "raw_records": records,
                "raw_cases": cases,
            }

        except httpx.HTTPStatusError as e:
            raise ToolException(
                code="JAVA_API_ERROR",
                message=f"Java 图谱查询接口返回错误: HTTP {e.response.status_code}"
            )
        except httpx.ConnectError:
            raise ToolException(
                code="JAVA_CONNECT_ERROR",
                message=f"无法连接 Java 后端服务: {self._base_url}"
            )
        except Exception as e:
            raise ToolException(
                code="GRAPH_JAVA_QUERY_FAILED",
                message=f"图谱诊断路径查询失败: {e}"
            )

    @staticmethod
    def _format_paths(records: list, keyword: str = None) -> str:
        """
        将 DiagnosisPathVO 列表格式化为 LLM 可读的图谱证据链文本。
        格式与 Java 端 BuildStringUtils.buildGraphContextAssembler() 对齐。
        """
        lines = []

        if keyword:
            lines.append(f"【设备线索】{keyword}")
            lines.append("")

        lines.append("【图谱证据链】")

        for i, path in enumerate(records):
            device_name = path.get("deviceName") or "未知设备"
            component_name = path.get("componentName") or "未知部件"
            fault_name = path.get("faultName") or "未知故障"
            fault_severity = path.get("faultSeverity") or "未知"
            path_text = path.get("pathText") or f"{device_name} → {component_name} → {fault_name}"
            match_score = path.get("matchScore", 0)

            lines.append(f"{i + 1}. {path_text}")
            lines.append(f"   设备：{device_name}")
            lines.append(f"   可能相关部件：{component_name}")
            lines.append(f"   匹配故障：{fault_name}")
            lines.append(f"   故障等级：{fault_severity}")
            lines.append(f"   匹配维度：{match_score}")

            # 解决方案列表
            solutions = path.get("solutions") or []
            if solutions:
                for j, sol in enumerate(solutions):
                    title = sol.get("title") or "暂无标题"
                    est_time = sol.get("estimatedTime")
                    verified = sol.get("verified")
                    status = sol.get("status", "active")
                    time_str = f"{est_time}分钟" if est_time else "未知"
                    verified_str = "已验证" if verified else "⚠未验证(手册推断)"
                    deprecated_suffix = " [已过期]" if status == "deprecated" else ""
                    lines.append(f"   方案{j + 1}：{title}（{time_str}，{verified_str}）{deprecated_suffix}")
            else:
                # 兼容旧字段
                sol_title = path.get("solutionTitle") or "暂无解决方案"
                est_time = path.get("estimatedTime")
                verified = path.get("verified")
                time_str = f"{est_time}分钟" if est_time else "未知"
                verified_str = "已验证" if verified else "⚠未验证(手册推断)"
                lines.append(f"   推荐方案：{sol_title}（{time_str}，{verified_str}）")

            lines.append("")

        lines.append("【回答要求】")
        lines.append("请优先依据图谱证据链回答。")
        lines.append("如果知识图谱证据不足，请说明需要进一步检查。")
        lines.append("不要编造知识图谱中不存在的部件、故障或解决方案。")

        return "\n".join(lines)

    @staticmethod
    def _format_cases(cases: list) -> str:
        """将相关沉淀案例格式化为 LLM 可读文本（一线人员审核入库的实战经验）。"""
        lines = ["【相关案例（一线沉淀，已审核）】"]
        for i, c in enumerate(cases):
            title = c.get("title") or "无标题案例"
            score = c.get("score")
            score_str = f"（相关度{round(score, 3)}）" if isinstance(score, (int, float)) else ""
            lines.append(f"{i + 1}. {title}{score_str}")
            summary = c.get("summary")
            if summary:
                lines.append(f"   摘要：{summary}")
            exp = c.get("experienceSummary")
            if exp:
                lines.append(f"   经验总结：{exp}")
            resolution = c.get("resolution")
            if resolution:
                lines.append(f"   解决过程：{resolution}")
            lines.append("")
        lines.append("案例为同类故障的真实处置经验，可作为图谱证据的实战补充参考。")
        return "\n".join(lines)


# ==================== 单例 ====================

class JavaGraphDeviceSearchTool(BaseTool):
    """Search devices through the Java graph service."""

    def __init__(self):
        self._settings = get_settings()
        self._base_url = self._settings.java_service_url

    @property
    def name(self) -> str:
        return "java_graph_device_search"

    @property
    def description(self) -> str:
        return (
            "Search device nodes from the maintenance knowledge graph through the Java backend. "
            "Use this when the device name is vague or incomplete and a device list is needed "
            "before diagnosis path search."
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword matching device name, code, model, or location",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of returned devices, default 10",
                    "default": 10,
                },
            },
            "required": ["keyword"],
        }

    async def _execute(self, keyword: str, limit: int = 10) -> dict:
        logger.info("[graph_java_tool] search devices: keyword=%s, limit=%d", keyword, limit)

        try:
            headers = {"X-Internal-Token": self._settings.internal_token}

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/weixiu/device/search",
                    params={"keyword": keyword, "limit": limit},
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()

            devices = result.get("data", [])
            formatted = []
            for device in devices:
                formatted.append({
                    "id": device.get("id"),
                    "name": device.get("name"),
                    "code": device.get("code"),
                    "model": device.get("model"),
                    "location": device.get("location"),
                    "manufacturer": device.get("manufacturer"),
                })

            device_names = [device["name"] for device in formatted if device.get("name")]
            logger.info(
                "[graph_java_tool] device search completed: found %d devices %s",
                len(formatted),
                device_names,
            )

            return {
                "count": len(formatted),
                "devices": formatted,
            }

        except httpx.ConnectError:
            raise ToolException(
                code="JAVA_CONNECT_ERROR",
                message=f"鏃犳硶杩炴帴 Java 鍚庣鏈嶅姟: {self._base_url}",
            )
        except Exception as e:
            raise ToolException(
                code="GRAPH_DEVICE_SEARCH_FAILED",
                message=f"鍥捐氨璁惧鎼滅储澶辫触: {e}",
            )


_diagnosis_path_tool: Optional[JavaGraphDiagnosisPathTool] = None
_device_search_tool: Optional[JavaGraphDeviceSearchTool] = None


def get_java_graph_diagnosis_path_tool() -> JavaGraphDiagnosisPathTool:
    global _diagnosis_path_tool
    if _diagnosis_path_tool is None:
        _diagnosis_path_tool = JavaGraphDiagnosisPathTool()
    return _diagnosis_path_tool


def get_java_graph_device_search_tool() -> JavaGraphDeviceSearchTool:
    global _device_search_tool
    if _device_search_tool is None:
        _device_search_tool = JavaGraphDeviceSearchTool()
    return _device_search_tool
