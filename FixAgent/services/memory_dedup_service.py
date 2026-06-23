"""
长期记忆语义去重（离线 pass）

【为什么需要】
去向量后，记忆去重只按字面 name 覆盖，防不住"同义不同名"——LLM 给同一件事
起了不同 slug 就并存两条语义重复的事实，越用越脏（漏洞#2）。

本服务由 Java MemorySemanticDedupScheduler 定时调用：传入某用户的活跃事实列表，
用小模型(qwen-turbo)找出【真正重复】的分组，返回合并方案 {keep, drop[]}；
Java 据此把非代表条 supersede（不物理删，留恢复窗口）。

【安全取向：宁可留重复，也不丢信息】
只合并"一条信息被另一条完全覆盖"的真重复；同主题但各有独有细节的，绝不合并。
拿不准就不并（精确率优先）。代表条(keep)必须能覆盖同组其余。

【调用】api/main.py 的 POST /ai/memory/dedup → dedup_facts(facts)
"""

import json
import logging

from config.settings import get_settings
from services.llm_service import get_llm_service

logger = logging.getLogger(__name__)


DEDUP_SYSTEM_PROMPT = """你是长期记忆去重助手。给你同一个用户的若干条「事实记忆」，找出其中【真正重复】的分组并给出合并方案。

输入每条格式：序号 | name | 类型 | 摘要 | 内容

## 什么算"真正重复"（只有满足才能合并）
多条指向同一件事，且其中一条的信息【完全覆盖】其余——被删的那条没有任何独有的、有价值的信息。
✓ 可合并：「3号泵额定压力22MPa」与「3号泵压力是22兆帕」——同一事实、仅措辞不同。
✗ 不可合并：「3号泵额定压力22MPa」与「3号泵5月换过密封圈」——同主题但各有独有信息，合并会丢信息。
✗ 不可合并：只是同一设备/同一主题但讲不同方面（参数 vs 维修记录 vs 故障历史）。

## 铁律：宁可不合并，也不要丢信息
- 拿不准是否真重复 → 不要放进同一组（精确率优先，漏并没关系，错并丢信息不行）。
- 一组里只要某条有【独有的、别条没有的】细节 → 它不能被删；要么整组不合并、要么把它选成代表。
- 代表条(keep) 必须是组内【信息最全、能覆盖其余全部】的那一条。

## 输出（只输出 JSON，不要其他内容）
{"groups": [{"keep": "代表条name", "drop": ["被并条name", ...], "reason": "判定为重复的依据"}]}
- 只输出确需合并的组；每组至少 2 条（1 个 keep + 至少 1 个 drop）。
- keep 与 drop 里的 name 必须来自输入；keep 不能同时出现在 drop 里。
- 没有可合并的，返回 {"groups": []}（这是完全正常的）。
"""


async def dedup_facts(facts: list[dict]) -> list[dict]:
    """
    对某用户的活跃事实做语义去重，返回合并方案 [{keep, drop:[...], reason}]。

    入参 facts: [{name, description, content, type, importance, turn_ts}, ...]
    失败/无可并 → 返回 []（绝不抛，不影响调度主流程）。
    """
    if not facts or len(facts) < 2:
        return []

    valid_names: set[str] = set()
    lines: list[str] = []
    for i, f in enumerate(facts, 1):
        name = str(f.get("name") or "").strip()
        if not name:
            continue
        valid_names.add(name)
        ftype = str(f.get("type") or "").strip()
        desc = str(f.get("description") or "").strip()
        content = str(f.get("content") or "").strip()
        lines.append(f"{i} | {name} | {ftype} | {desc} | {content}")

    if len(valid_names) < 2:
        return []

    user_content = "以下是该用户的长期记忆事实，请按规则找出【真正重复】的分组：\n\n" + "\n".join(lines)
    settings = get_settings()
    try:
        result = await get_llm_service().chat(
            messages=[
                {"role": "system", "content": DEDUP_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            model=settings.intent_router_model,  # qwen-turbo，便宜
        )
        content = (result or {}).get("content") or ""
        data = json.loads(content)
        groups = data.get("groups") or []
    except Exception as e:
        logger.warning("[memory_dedup] 抽取/解析异常 err=%s", e)
        return []

    if not isinstance(groups, list):
        return []

    # 校验 + 防越界：name 必须有效；keep 不在 drop；drop 去重；跨组不得有 name 交叠（有交叠则保守跳过整组）
    cleaned: list[dict] = []
    used: set[str] = set()
    for g in groups:
        if not isinstance(g, dict):
            continue
        keep = str(g.get("keep") or "").strip()
        if keep not in valid_names:
            continue
        drop_raw = g.get("drop") or []
        if not isinstance(drop_raw, list):
            continue
        drop = []
        for d in drop_raw:
            d = str(d).strip()
            if d in valid_names and d != keep and d not in drop:
                drop.append(d)
        if not drop:
            continue
        names = [keep] + drop
        if any(n in used for n in names):
            # 与已采纳的组有 name 交叠 → 保守跳过，避免一条被多组处理
            continue
        used.update(names)
        cleaned.append({"keep": keep, "drop": drop, "reason": str(g.get("reason") or "")[:200]})

    return cleaned
