"""
标准作业流程推荐工具（通过 Java 端预留接口）。

调用 Java 后端 /weixiu/procedure/recommend 接口，根据设备类型和检修等级
获取已维护的标准作业流程，供 FixAgent 在对话中推荐检修指引。
"""

import logging
from typing import Optional

import httpx

from config.settings import get_settings
from tools.base_tool import BaseTool, ToolException

logger = logging.getLogger(__name__)


class ProcedureRecommendTool(BaseTool):
    """通过 Java 后端推荐标准作业流程。"""

    def __init__(self):
        settings = get_settings()
        self._base_url = settings.java_service_url
        self._internal_token = settings.internal_token

    @property
    def name(self) -> str:
        return "procedure_recommend"

    @property
    def description(self) -> str:
        return (
            "根据设备类型和故障信息推荐标准作业流程。"
            "当用户需要检修指引且能够确定设备类型时使用；"
            "返回匹配的标准流程名称、检修等级、预计耗时和说明。"
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "device_type": {
                    "type": "string",
                    "description": "设备类型，例如发动机总成",
                },
                "maintenance_level": {
                    "type": "string",
                    "description": "检修等级，例如一级检修，可选",
                },
                "fault_description": {
                    "type": "string",
                    "description": "故障描述，可选，用于说明推荐上下文",
                },
            },
            "required": ["device_type"],
        }

    async def _execute(
        self,
        device_type: str,
        maintenance_level: str = None,
        fault_description: str = None,
    ) -> dict:
        device_type = (device_type or "").strip()
        if not device_type:
            return {
                "procedures_found": 0,
                "context": "【标准流程推荐结果】\n缺少设备类型，无法推荐标准作业流程。",
            }

        params = {"deviceType": device_type}
        if maintenance_level and maintenance_level.strip():
            params["maintenanceLevel"] = maintenance_level.strip()

        try:
            logger.info(
                "[procedure_recommend_tool] 调用 Java 流程推荐: device_type=%s, maintenance_level=%s",
                device_type,
                params.get("maintenanceLevel"),
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self._base_url}/weixiu/procedure/recommend",
                    params=params,
                    headers={"X-Internal-Token": self._internal_token},
                )
                response.raise_for_status()
                result = response.json()

            procedures = self._extract_procedures(result)
            return {
                "procedures_found": len(procedures),
                "context": self._format_procedures(
                    procedures,
                    device_type,
                    params.get("maintenanceLevel"),
                    fault_description,
                ),
            }
        except httpx.HTTPStatusError as exc:
            raise ToolException(
                code="JAVA_API_ERROR",
                message=f"Java 标准流程推荐接口返回错误: HTTP {exc.response.status_code}",
            )
        except httpx.ConnectError:
            raise ToolException(
                code="JAVA_CONNECT_ERROR",
                message=f"无法连接 Java 标准流程推荐接口: {self._base_url}",
            )
        except Exception as exc:
            raise ToolException(
                code="PROCEDURE_RECOMMEND_FAILED",
                message=f"标准流程推荐失败: {exc}",
            )

    @staticmethod
    def _extract_procedures(result: dict) -> list:
        data = result.get("data", []) if isinstance(result, dict) else []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("records", "procedures", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def _format_procedures(
        procedures: list,
        device_type: str,
        maintenance_level: Optional[str] = None,
        fault_description: Optional[str] = None,
    ) -> str:
        lines = ["【标准流程推荐结果】", f"设备类型：{device_type}"]
        if maintenance_level:
            lines.append(f"检修等级：{maintenance_level}")
        if fault_description and fault_description.strip():
            lines.append(f"故障描述：{fault_description.strip()}")
        lines.append("")

        if not procedures:
            lines.append("未找到匹配的标准作业流程。")
            lines.append("可根据知识库与图谱证据给出一般性检查建议，并说明暂无标准流程匹配。")
            return "\n".join(lines)

        for index, procedure in enumerate(procedures, start=1):
            name = procedure.get("name") or procedure.get("procedureName") or "未命名流程"
            level = procedure.get("maintenanceLevel") or "未标注"
            duration = procedure.get("estimatedDuration")
            description = procedure.get("description")
            duration_text = f"{duration}分钟" if duration is not None else "未标注"
            lines.append(f"{index}. {name}")
            lines.append(f"   检修等级：{level}")
            lines.append(f"   预计耗时：{duration_text}")
            if description:
                lines.append(f"   说明：{description}")
            lines.append("")

        lines.append("请基于上述匹配流程向用户说明推荐理由，并引导其在检修任务模块启动流程。")
        return "\n".join(lines)


_procedure_recommend_tool: Optional[ProcedureRecommendTool] = None


def get_procedure_recommend_tool() -> ProcedureRecommendTool:
    global _procedure_recommend_tool
    if _procedure_recommend_tool is None:
        _procedure_recommend_tool = ProcedureRecommendTool()
    return _procedure_recommend_tool
