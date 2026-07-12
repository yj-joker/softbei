# -*- coding: utf-8 -*-
"""
部件反查设备工具（四态诊断-状态2）

用户只描述部件没说设备时，通过部件描述向量召回 Component，
再反查所属 Device，返回"设备+部件"组合列表。

Agent 编排层根据返回数量决策：
- 唯一设备 → 自动锁定，继续诊断
- 多设备 → 反问用户澄清
- 0设备 → 降级（图谱无此部件）
"""

import logging
from typing import List, Dict

import httpx

from tools.base_tool import BaseTool, ToolException
from config.settings import get_settings

logger = logging.getLogger(__name__)


class ComponentReverseDeviceTool(BaseTool):
    """部件反查设备工具"""

    def __init__(self):
        self._settings = get_settings()
        self._base_url = self._settings.java_service_url

    @property
    def name(self) -> str:
        return "component_reverse_device"

    @property
    def description(self) -> str:
        return (
            "当用户描述部件故障但未明确说明设备时使用。"
            "通过部件描述反查所属设备，返回设备+部件组合列表。"
            "用于判断是否需要向用户反问澄清设备，或自动锁定唯一设备继续诊断。"
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "component_description": {
                    "type": "string",
                    "description": "部件描述，从用户输入中提取部件相关内容"
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量上限，默认10",
                    "default": 10
                }
            },
            "required": ["component_description"]
        }

    async def _execute(self, component_description: str, limit: int = 10) -> dict:
        if not component_description or not component_description.strip():
            return {"device_count": 0, "devices": [], "message": "部件描述为空"}

        try:
            logger.info("[component_reverse_device] 反查设备: component_desc=%s, limit=%d",
                        component_description, limit)

            headers = {"X-Internal-Token": self._settings.internal_token}

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/weixiu/path/reverse-device",
                    params={
                        "componentDescription": component_description,
                        "limit": limit,
                        "minScore": 0.70
                    },
                    headers=headers
                )
                resp.raise_for_status()
                result = resp.json()

            devices = result.get("data", [])
            device_count = len(devices)

            logger.info("[component_reverse_device] 反查完成: 找到 %d 个设备+部件组合", device_count)

            if device_count == 0:
                return {
                    "device_count": 0,
                    "devices": [],
                    "message": "知识图谱中未找到匹配的部件。图谱覆盖范围有限，可能该部件未录入。"
                }

            # 按设备分组去重（同设备多部件取分数最高的）
            device_map: Dict[str, dict] = {}
            for item in devices:
                device_id = item.get("deviceId")
                if device_id not in device_map or item.get("score", 0) > device_map[device_id].get("score", 0):
                    device_map[device_id] = item

            unique_devices = list(device_map.values())
            unique_count = len(unique_devices)

            logger.info("[component_reverse_device] 唯一设备数: %d", unique_count)

            return {
                "device_count": unique_count,
                "devices": unique_devices,
                "message": self._format_message(unique_count, unique_devices),
                "raw_all": devices
            }

        except httpx.HTTPStatusError as e:
            raise ToolException(
                code="JAVA_API_ERROR",
                message=f"Java 反查设备接口返回错误: HTTP {e.response.status_code}"
            )
        except httpx.ConnectError:
            raise ToolException(
                code="JAVA_CONNECT_ERROR",
                message=f"无法连接 Java 后端服务: {self._base_url}"
            )
        except Exception as e:
            raise ToolException(
                code="COMPONENT_REVERSE_DEVICE_FAILED",
                message=f"部件反查设备失败: {e}"
            )

    @staticmethod
    def _format_message(device_count: int, devices: List[dict]) -> str:
        if device_count == 0:
            return "知识图谱中未找到匹配的部件。"
        elif device_count == 1:
            device = devices[0]
            device_name = device.get("deviceName", "未知设备")
            component_name = device.get("componentName", "未知部件")
            return (
                f"检测到唯一设备：{device_name}\n"
                f"匹配部件：{component_name}\n"
                f"建议：自动锁定该设备，继续诊断路径查询。"
            )
        else:
            lines = [f"检测到 {device_count} 个可能的设备，需要用户澄清：\n"]
            for i, device in enumerate(devices[:5], 1):
                device_name = device.get("deviceName", "未知设备")
                component_name = device.get("componentName", "未知部件")
                location = device.get("deviceLocation")
                loc_str = f"（位置：{location}）" if location else ""
                lines.append(f"{i}. {device_name} 的 {component_name}{loc_str}")

            if device_count > 5:
                lines.append(f"... 还有 {device_count - 5} 个设备")

            lines.append("\n建议：反问用户「你说的是哪个设备的这个部件？」，列出上述选项供用户选择。")
            return "\n".join(lines)


_reverse_device_tool = None


def get_component_reverse_device_tool() -> ComponentReverseDeviceTool:
    global _reverse_device_tool
    if _reverse_device_tool is None:
        _reverse_device_tool = ComponentReverseDeviceTool()
    return _reverse_device_tool
