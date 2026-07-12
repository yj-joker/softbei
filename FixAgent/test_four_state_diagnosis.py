# -*- coding: utf-8 -*-
"""
四态诊断测试脚本：状态2 部件反查设备

测试场景：
1. 唯一设备 → 自动锁定
2. 多设备 → 反问用户
3. 0设备 → 降级
"""

import asyncio
import sys
import httpx

sys.path.insert(0, '.')

from tools.component_reverse_device_tool import get_component_reverse_device_tool


async def test_reverse_device():
    tool = get_component_reverse_device_tool()

    print("=" * 60)
    print("测试1：唯一设备场景（摩托车发动机部件）")
    print("=" * 60)

    result1 = await tool.run(component_description="气缸")
    data1 = result1.data if hasattr(result1, 'data') else result1
    print(f"设备数量: {data1.get('device_count')}")
    print(f"消息:\n{data1.get('message')}")
    if data1.get('devices'):
        print(f"设备列表: {[d.get('deviceName') for d in data1['devices']]}")

    print("\n" + "=" * 60)
    print("测试2：直接调用 Java 接口验证")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "http://localhost:8080/weixiu/path/reverse-device",
            params={
                "componentDescription": "气缸",
                "limit": 10,
                "minScore": 0.70
            },
            headers={"X-Internal-Token": "test-token"}
        )
        print(f"HTTP状态: {resp.status_code}")
        data = resp.json()
        print(f"返回数据: {data.get('code')}, 设备数: {len(data.get('data', []))}")
        if data.get('data'):
            for item in data['data'][:3]:
                print(f"  - {item.get('deviceName')} / {item.get('componentName')} (分数: {item.get('score')})")

    print("\n" + "=" * 60)
    print("测试3：不存在的部件（0设备场景）")
    print("=" * 60)

    result3 = await tool.run(component_description="不存在的神秘部件XYZABC")
    data3 = result3.data if hasattr(result3, 'data') else result3
    print(f"设备数量: {data3.get('device_count')}")
    print(f"消息:\n{data3.get('message')}")


if __name__ == "__main__":
    asyncio.run(test_reverse_device())
