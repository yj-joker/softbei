"""
工作记忆整理 Agent

将多条原始对话记录压缩为结构化记忆摘要。
Java 端在对话达到阈值（如30条）时触发整理，调用本 Agent 提取关键信息。

采用单次 function calling 架构：
LLM 读取对话 → 提取候选事实 → 调用 search_similar_facts 检索 → 判断冲突 → 输出结果

【与其他模块的关系】
- 继承 BaseAgent，覆盖 run() 实现 function calling 流程
- 由 api/main.py 的 /ai/memory/consolidate 端点调用
- 调用 services/llm/service.py 的 chat_with_tools()
"""

import json
import logging
import re
import time

from agents.base_agent import BaseAgent, AgentInput, AgentOutput
from pydantic import ValidationError
from schemas.memory import MemorySummary

logger = logging.getLogger(__name__)


MEMORY_SYSTEM_PROMPT = """你是工作记忆整理助手。从对话记录中提取并整理记忆，输出结构化结果。

## ⚠️ 最重要的规则（违反此规则等于输出无效）
- 事实、偏好、未完成事项 **只能从【用户】发言中提取**
- 【助手】发言是AI生成的回复，**绝对不能**从中提取事实、偏好或待办
- 助手说的内容仅用于理解对话上下文，不代表用户的观点或需求
- 如果助手建议了某个方案/做法，除非用户明确认可，否则不算用户偏好或待办
- **绝对禁止将助手的建议/方案/步骤推断为用户的计划或待办！**
  ✗ 用户说"想吃蛋糕"，助手回复"如何制作蛋糕" → 不能记录"用户打算制作蛋糕"
  ✗ 用户问"电机异响怎么办"，助手回复"建议更换轴承" → 不能记录"用户计划更换轴承"
  ✓ 只有用户自己说"我准备去换轴承"才能记录为待办

## 去重依据
用户消息下方的「## 已有事实索引」列出了该用户长期记忆中**已经存在**的事实（格式：- [name] (type) — 摘要）。
判断某条候选事实是否重复/是否需更新，**以该索引为准**：同一件事复用相同 name 覆盖，全新的事才用新 name 新增。
（search_similar_facts 工具已停用，不要依赖它的返回；若索引为空说明该用户暂无历史事实。）

## 分类标准

### 事实（客观、已确认、可独立理解的信息）

**属于事实：** 用户提到的设备型号/参数/配置、用户描述的诊断过程和结果、用户确认的技术结论、用户项目/系统的客观信息
**不属于事实（绝对不能记录）：**
- 主观评价、工作习惯、未完成的任务
- 助手/AI输出的任何内容：包括解释、建议、方案、步骤、知识性回答
  ✗ 助手说"蛋糕需要面粉和鸡蛋" → 不是用户的事实
  ✗ 助手说"建议用型号A" → 不是事实（除非用户说"好，就用型号A"）
  ✗ 助手解释了某个原理 → 不是事实
- 只有用户亲口陈述或明确确认的信息才是事实

**事实提取规则（非常重要）：**
1. 自包含：每条事实必须脱离对话上下文也能完整理解
   ✗ "他说用那个框架"
   ✓ "用户的项目使用 Spring Boot 3.2 框架"
2. 原子化：每条事实只描述一件事
   ✗ "用户在做维修系统，用Java和MySQL"
   ✓ "用户正在开发一个设备维修管理系统"
   ✓ "用户的后端技术栈是 Java"
   ✓ "用户使用 MySQL 作为数据库"
3. 时效标注：如果事实可能随时间改变，加上时间标记
   ✓ "用户当前正在调试登录模块的bug（2026-05）"
4. **重要度判断（宁缺毋滥！）** — 只记录有长期价值的事实：
   ✓ 值得记录：设备型号、技术架构、项目名称、确认的结论
   ✗ 不要记录：当前正在调试的临时状态、对话中的过渡性表述、用户的随口一提
   判断标准：**如果这条信息在下周还有用，就记录；如果只在今天有用，就不记录**
5. **重要度评分（importance, 1-10）：**
   - 1-3：临时信息（当前调试状态、过渡性表述）
   - 4-6：一般技术细节（某个配置项的值、一次性操作结果）
   - 7-9：重要信息（设备型号、系统架构、关键故障结论）
   - 10：核心信息（安全规程、多次确认的关键事实）
6. **置信度评分（confidence, 0-1）：**
   - 0.90-1.00：用户明确、反复确认的信息
   - 0.70-0.89：用户正常陈述，无矛盾
   - 0.50-0.69：从上下文推断，可能需要确认
   - < 0.50：不确定，最好不要提取
7. **业务维度标注（维修场景专用）：**
   如果事实明显与特定设备/场地/任务相关，请标注：
   - device_type: 设备类型名称（如"液压泵"、"曲轴"、"变速箱"）
   - equipment_id: 如果对话中提到了具体设备编号/ID
   - site_id: 如果对话中提到了具体场地编号/ID
   - task_id: 如果是在某个检修任务讨论中产生的事实
   如果事实是通用性的（不特定于某设备/场地），所有维度留空字符串。
   不确定时宁可留空，不要猜测。
8. **记忆索引字段（每条事实必须产出，用于"文件式记忆索引"寻址与展示）：**
   - name: 简短稳定的英文/拼音 slug（如 `device-x2012-model`、`bearing-replace-rule`）。同一事实复用同名（用于按 name 寻址/去重），只用小写字母/数字/连字符。
   - description: 一句话钩子（≤30字），供"记忆索引"列表展示，让模型快速判断该条是否与当前问题相关。
   - type: 三选一 —— `feedback`(要遵守的规则,如安全/操作约束) | `project`(客观事实) | `reference`(指向别处去查的指针)。无法判断时默认 `project`。
   - why（可空）: 该规则/事实为何成立，指向可证伪的外部条件 —— 主要给 `feedback` 用；`project`/`reference` 通常留空。
   - how_to_apply（可空）: 何时适用 / 失效信号（即什么情况下这条不再适用）—— 主要给 `feedback` 用；其他类型通常留空。

### 偏好（用户主动表达的主观倾向，需要严格区分）

**【是偏好 —— 必须满足以下任一条件才能记录】**
1. 用户的明确指令："以后回答用中文"、"不要给我写注释"、"回复简洁一点"
2. 用户纠正AI行为后的隐含要求：AI用英文回复后用户说"说中文" → 偏好中文
3. 用户主动表达的工作习惯："我习惯先写测试再写代码"
4. 用户主动表达的好恶："我不喜欢用Lombok"、"我更喜欢函数式写法"

**【不是偏好 —— 绝对不要记录为偏好】**
- 助手/AI说的任何内容 → 绝不是用户偏好！（即使助手建议了某种方式）
- 用户正在讨论/使用的技术 ≠ 偏好该技术
  "帮我看看这个Java代码怎么改" → 不是偏好，只是当前任务涉及Java
  "用Python写个脚本" → 不是偏好，只是一次性任务需求
- 用户提到但未表达态度的事物
  "React的虚拟DOM是什么原理" → 不是偏好，只是在提问
- 对话的主题/领域
  一整段关于数据库优化的讨论 → 不代表偏好数据库，只是当前话题
- 助手的自我介绍或能力说明 → 不是事实也不是偏好

**【sourceType 标注】每条偏好必须标注来源类型：**
- "explicit"：用户直接说出来的指令或态度（如"不要写注释"、"我喜欢详细解释"）
- "inferred"：从用户反复出现的行为模式推断的（如用户多次追问细节→可能偏好详细回复）
  注意：单次行为不足以推断偏好，需要有多次一致的模式

**【preferenceCategory 判断规则】**
- 0（用户级）：涉及个人习惯、跨话题通用的偏好，如回复语言、风格习惯
- 1（会话级）：仅针对本次具体任务的临时偏好，如"这次用表格形式展示"

### 未完成事项（悬而未决的待办）

**【是待办 —— 用户自己明确表达的行动意图】**
- 用户说"我明天去修电机" → ✓ 用户计划明天修电机
- 用户说"我待会儿试试重启" → ✓ 用户打算重启设备
- 用户问了但没得到答案的问题 → ✓ 未答复问题

**【不是待办 —— 绝对不要记录】**
- 助手建议的方案/步骤 → ✗ 不是用户的计划！
  用户问"怎么办"，助手说"建议换轴承" → 不能记录"用户打算换轴承"
- 助手描述的操作流程 → ✗ 不是用户的待办！
  助手回复"第一步拆开外壳，第二步检查线路" → 不能记录为用户的行动计划
- 用户随口提到的愿望/想法（没有行动意图） → ✗
  "想吃蛋糕" ≠ "打算制作蛋糕"，除非用户明确说"我要自己做一个蛋糕"
- 助手推荐的任何东西 → ✗ 除非用户回复"好的我去做"

**核心判断标准：用户是否用自己的话表达了"我要做/我打算做/我准备做"？**
如果用户没有这样说，就不是待办。不要从助手的回复中推断用户意图。

注意：一旦事项在新对话中得到解决/被放弃，把它在「## 已有未完成事项」里的 name 放入 resolved_unresolved_names（系统会关闭它）。

## 冲突判断规则（仅针对事实）
对照上方「## 已有事实索引」判断：
- 索引里没有这条事实 → 用新 name 正常新增
- 索引里已有同一件事、内容也相同 → 不要重复输出（不放进 new_facts）
- 索引里已有同一件事、但结论不同/有更新 → new_facts 里**复用相同 name**、content 写最新内容（系统按 name 就地覆盖，自动体现变更，无需 superseded_ids）
- 索引为空 → 一律按新增处理

未完成事项去重/关闭规则（对照「## 已有未完成事项」按 name 处理）：
- 是同一件待办（已在上面列出）→ updated_unresolved 里**复用相同 name**（系统按 name 去重，不新增重复）
- 上面没有的新待办 → 用一个新的稳定 name 加入 updated_unresolved
- 上面某条已在本段对话中解决/放弃 → 把它的 name 放入 resolved_unresolved_names
- 上面已有、本段没有新进展的待办 → 不必重复输出
- status: active=进行中, superseded=已放弃
  - id: 数据库主键，用于精确标记已解决的事项

## 提取质量门控（最终检查清单）
输出前，请逐条检查每个提取的条目：
1. ✅ 这条信息是从【用户】发言中提取的吗？（如果是从助手发言推断的 → 删除）
2. ✅ 如果是事实：下周还有参考价值吗？（如果只是当前调试的临时状态 → 删除）
3. ✅ 如果是待办：用户是否亲口说了"我要做/打算做"？（如果是助手建议的 → 删除）
4. ✅ 如果是偏好：是持久性的还是一次性的？（"这次用英文" ≠ 永久偏好 → 删除）

**宁缺毋滥原则：** 如果不确定是否应该提取，就不要提取。
错误记忆比没有记忆危害更大。空的 new_facts/updated_preferences/updated_unresolved 是完全正常的。

## 摘要要求（重要：摘要只管"最近在干什么"，不管"历史有什么"）
brief_summary 承接的是**最近这段对话的线索与当前意图**——比如当前在排查/讨论什么、聊到哪一步、还悬着什么尚未沉淀成事实的上下文。100字以内。

- **不要把它当成全历史的话题清单来累积。** 所有值得长期记住的原子事实都已进入"已有事实索引"（name+摘要、全历史、不丢失、每轮注入），那才是话题导航层。摘要里**不要重复罗列已经成为事实/偏好/待办的内容**。
- 收到"之前的对话背景"（上一版摘要）时，**不是往上叠加堆积**，而是**滚动更新**：丢掉已经沉淀为事实、或已不再相关的旧话题，只保留仍在延续的近期线索 + 本段新进展。宁可短，不要硬塞。
- 如果本段对话没有需要承接的连续线索（事实都抽走了、没有悬而未决的思路），brief_summary 可以很短甚至只是一句话概述，这是正常的。

**判断方法：你写的每一句，先问"这条是不是已经进了 new_facts / updated_unresolved / 偏好？" 是 → 删掉不要写进摘要。** 摘要写的是"它们之外"的连贯线索（在排查什么、聊到哪步、用户当前关注点）。
反例（❌ 把已抽成事实的内容又罗列一遍）：
  "用户更正3号泵压力为22MPa；确认风机皮带挠度10mm；轴承更换已完成。" —— 这三件都已是事实/待办，摘要里全是冗余复述。
正例（✅ 只留线索/状态，不复述事实清单）：
  "围绕几台设备的参数与一项检修待办做了登记与更正，当前无悬而未决事项。"
  或在没有延续线索时直接写："本段为零散的参数登记，无待续话题。"

## 输出格式
严格按以下 JSON 输出，不要输出其他内容：
```json
{
  "new_facts": [
    {"content": "自包含的事实描述", "keywords": "检索用关键词", "source_seq_range": "3-5", "importance": 7, "confidence": 0.85, "device_type": "", "equipment_id": "", "site_id": "", "task_id": "", "name": "device-x2012-model", "description": "X2012型号设备的关键参数", "type": "project", "why": "", "how_to_apply": ""}
  ],
  "superseded_ids": ["要标记为无效的旧事实ID"],
  "updated_preferences": [
    {"content": "偏好描述", "category": "交互风格|格式要求|工作习惯|关注领域|其他", "preferenceCategory": 0, "sourceType": "explicit"}
  ],
  "updated_unresolved": [
    {"name": "check-pump-3-seal", "content": "待解决描述", "type": "未答复问题|进行中任务|用户待办", "status": "active"}
  ],
  "resolved_unresolved_names": ["本轮已解决的未决事项的 name"],
  "brief_summary": "100字以内，最近这段的线索/当前意图（不累积罗列已成为事实的历史话题）"
}
```
"""


class MemoryAgent(BaseAgent):
    """
    工作记忆整理 Agent

    单次 function calling 架构：
    1. 构建消息（含已有偏好/未完成 + 新对话）
    2. 注册 search_similar_facts 工具
    3. 调用 LLM（自动处理工具调用循环）
    4. 解析 JSON 返回结构化数据
    """

    @property
    def name(self) -> str:
        return "memory_agent"

    @property
    def description(self) -> str:
        return "工作记忆整理Agent：将多条原始对话压缩为结构化摘要"

    def get_system_prompt(self) -> str:
        return MEMORY_SYSTEM_PROMPT

    def _format_conversations(self, conversations: list) -> str:
        """
        将对话列表格式化为 LLM 可读的文本块。
        使用明确的分隔符和标注，帮助LLM区分用户发言和助手发言。
        """
        lines = ["## 新对话记录"]
        lines.append("（⚠️ 只从【用户】发言中提取事实、偏好和待办。【助手】发言仅作为上下文参考，绝不从中提取任何内容。助手的建议/方案不代表用户意图！）\n")
        for item in conversations:
            seq = item.get("seq", "?")
            content = item.get("content", "")
            if item.get("role") == "user":
                lines.append(f"━━━ 第{seq}轮 ━━━")
                lines.append(f"【用户】{content}")
            else:
                lines.append(f"【助手】{content}")
                lines.append("")  # 轮次间空行
        return "\n".join(lines)

    def _build_messages(self, input_data: AgentInput) -> list:
        """
        构建消息列表，包含已有记忆上下文和待整理的新对话。

        组装顺序：
        1. 之前的对话背景（上一版摘要，用于滚动更新最近线索）
        2. 已有偏好表格（让LLM知道哪些偏好已经存在，避免重复提取）
        3. 已有未完成事项表格（让LLM判断哪些已经在新对话中解决了）
        4. 新对话记录（本次需要整理的原始对话）
        """
        ctx = input_data.context or {}
        conversations = ctx.get("conversations", [])
        old_preferences = ctx.get("old_preferences", [])
        old_unresolved = ctx.get("old_unresolved", [])
        # 从Java端传来的上一轮整合产出的摘要，用于滚动更新最近线索（非累积堆积）
        previous_summary = ctx.get("previous_summary")
        # 该用户现有事实索引（name + type + description），用于去重：复用同名 / 标记 superseded
        existing_facts = ctx.get("existing_facts", "")

        parts = []

        # 如果有上一轮摘要，放在最前面作为对话背景
        # 滚动更新最近线索：丢掉已沉淀为事实/不再相关的旧话题，不要往上累积堆积
        if previous_summary:
            parts.append("## 之前的对话背景（上一版摘要，仅供承接最近线索）\n")
            parts.append(previous_summary)
            parts.append("")
            parts.append(
                "请滚动更新摘要：保留仍在延续的近期线索，丢掉已成为事实/已不相关的旧话题，"
                "不要把历史话题往上累积堆积（话题导航由"
                "下方'已有事实索引'承担）。\n"
            )

        if old_preferences:
            parts.append("## 已有偏好（需与对话中的新偏好合并）\n")
            parts.append("| 偏好内容 | 分类 | 级别 |")
            parts.append("|----------|------|------|")
            for p in old_preferences:
                level = "用户级" if p.get('preferenceCategory') == 0 else "会话级"
                parts.append(f"| {p.get('content', '')} | {p.get('category', '其他')} | {level} |")
            parts.append("")

        if existing_facts:
            parts.append("## 已有事实索引（该用户长期记忆中已存在的事实，格式：- [name] (type) — 摘要）\n")
            parts.append(existing_facts.rstrip())
            parts.append("")
            parts.append(
                "去重规则（重要）：\n"
                "- 新对话里的某条事实，若与上面某条 [name] 是**同一件事**（含结论被更新/纠正的情况）→ new_facts 里**复用那个完全相同的 name**，content 写最新内容。系统会按 name 就地覆盖更新，既不重复也能体现变更。\n"
                "- 上面已有、本轮没有新增信息的事实 → **不要**重复输出到 new_facts。\n"
                "- 只有上面完全没有的事实，才用一个新的 name 新增。\n"
            )

        if old_unresolved:
            # 带上 name 列：复用同名去重，解决时把 name 放进 resolved_unresolved_names
            parts.append("## 已有未完成事项（该用户当前未闭合的待办，按 name 去重/关闭）\n")
            parts.append("| name | 事项内容 | 类型 |")
            parts.append("|------|----------|------|")
            for u in old_unresolved:
                parts.append(f"| {u.get('name', '')} | {u.get('content', '')} | {u.get('type', '待办')} |")
            parts.append("")

        parts.append(self._format_conversations(conversations))

        user_content = "\n".join(parts)

        return [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": user_content}
        ]

    def _extract_json(self, text: str) -> MemorySummary:
        """
        从 LLM 返回内容中提取 JSON（兼容 markdown 代码块包裹），Pydantic 校验

        防御措施：
        1. 去除 markdown 代码块包裹
        2. json.loads 后校验是否为 dict（JSON 规范允许纯数字/字符串，但我们只要对象）
        3. 如果 LLM 返回了嵌套结构（如把结果包在某个 key 下），尝试自动提取
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)

        # 防御：json.loads 对纯数字、字符串、数组都能成功解析，
        # 但 MemorySummary(**data) 需要 dict，这里显式检查
        if not isinstance(data, dict):
            raise ValueError(
                f"LLM返回的JSON类型为 {type(data).__name__}，期望 dict 对象。"
                f"原始内容前100字符: {text[:100]}"
            )

        return MemorySummary(**data)

    async def _store_facts_to_vector(self, facts: list[dict], session_id: str):
        """
        [去向量] 不再写入 Redis 向量库。

        文件式记忆协议下事实改由 MySQL + 索引召回承载，事实不再向量化。
        本方法仅为每条事实生成稳定 doc_id，维持与 Java 端 factId 的一一对应
        （Java 用其作为 MySQL memory_fact.fact_id）。

        Args:
            facts: LLM 输出的 new_facts 列表
            session_id: 会话ID，用于 doc_id 前缀

        Returns:
            生成的 doc_id 列表，与 facts 一一对应
        """
        if not facts:
            return []
        batch_ts = str(int(time.time() * 1000))
        return [f"fact:{session_id}:{batch_ts}_{i}" for i in range(len(facts))]

    async def _call_llm_with_tools(self, messages, tools, tool_handlers, response_format):
        """
        封装 LLM 调用，供 run() 使用，方便重试时复用

        Returns:
            str: LLM 返回的文本内容
        """
        response = await self.llm_service.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_handlers=tool_handlers,
            response_format=response_format
        )
        return response.get("content") or ""

    async def run(self, input_data: AgentInput) -> AgentOutput:
        """
        执行记忆整理（function calling 模式），带自动重试

        流程：构建消息 → 注册工具 → chat_with_tools → 解析 JSON
        如果 LLM 返回垃圾数据（非 JSON 对象），自动重试1次。
        Qwen 模型偶尔会返回纯数字或乱码，重试通常能恢复。
        """
        start_time = time.time()
        max_retries = 1  # 最多重试1次（共2次调用）

        messages = self._build_messages(input_data)

        # [去向量] search_similar_facts 已停用（查空的事实向量库）。去重改由
        # _build_messages 注入的「已有事实索引」+ 提示词 + Java 端 (user_id,name) upsert 承载，不再注册工具。
        tools = []
        tool_handlers = {}

        # ========== LLM 调用 + 重试 ==========
        content = ""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                content = await self._call_llm_with_tools(
                    messages, tools, tool_handlers,
                    response_format={"type": "json_object"}
                )
            except Exception as e:
                # LLM 调用本身失败（网络/超时等）
                last_error = e
                logger.warning(f"[memory] LLM调用失败 attempt={attempt+1}: {e}")
                if attempt < max_retries:
                    continue
                latency_ms = int((time.time() - start_time) * 1000)
                return AgentOutput(
                    agent_name=self.name,
                    message="记忆整理失败，请稍后重试",
                    intention=None,
                    tools_used=[],
                    metadata={
                        "status": "error",
                        "error_type": type(e).__name__,
                        "error_detail": str(e),
                        "latency_ms": latency_ms
                    },
                    latency_ms=latency_ms
                )

            # 尝试解析 JSON
            try:
                summary = self._extract_json(content)
                last_error = None
                break  # 解析成功，跳出重试循环
            except (json.JSONDecodeError, ValueError, ValidationError, AttributeError, TypeError) as e:
                last_error = e
                logger.warning(
                    f"[memory] JSON解析失败 attempt={attempt+1}/{max_retries+1}: {e}, "
                    f"raw content: {content[:100]}"
                )
                if attempt < max_retries:
                    # 重试前重新构建 messages（避免上一轮 tool_call 残留）
                    messages = self._build_messages(input_data)
                    continue

        latency_ms = int((time.time() - start_time) * 1000)

        # 所有重试用完仍失败
        if last_error is not None:
            return AgentOutput(
                agent_name=self.name,
                message="记忆整理失败：LLM返回格式异常，已重试仍失败",
                intention=None,
                tools_used=[],
                metadata={
                    "status": "error",
                    "error_type": "JsonParseError",
                    "error_detail": f"LLM返回内容无法解析为记忆摘要: {str(last_error)[:200]}",
                    "raw_content": content[:200] if content else "",
                    "latency_ms": latency_ms,
                    "attempts": max_retries + 1
                },
                latency_ms=latency_ms
            )

        # ========== 解析成功，存入向量库 ==========
        fact_ids = []
        if summary.new_facts:
            try:
                fact_ids = await self._store_facts_to_vector(
                    [f.model_dump() for f in summary.new_facts],
                    input_data.session_id
                )
            except Exception:
                logger.exception("Failed to store facts to Redis vector DB")

        # 将向量库生成的 doc_id 附加到 summary 输出中
        # Java 端用这些 ID 作为 MySQL 的 factId，确保两端 ID 一致
        # by_alias=True：输出 camelCase（newFacts/briefSummary/...），对齐 Java MQ 整合读取的 key。
        # 此前用默认 snake_case，导致 Java 全部读成 null（摘要不写、事实不存），是整合静默失效的根因。
        summary_dict = summary.model_dump(by_alias=True)
        summary_dict["fact_ids"] = fact_ids

        return AgentOutput(
            agent_name=self.name,
            message=summary.brief_summary,
            intention=None,
            tools_used=[],
            metadata={
                "summary": summary_dict,
                "latency_ms": latency_ms
            },
            latency_ms=latency_ms
        )


# 单例
_memory_agent = None


def get_memory_agent() -> MemoryAgent:
    global _memory_agent
    if _memory_agent is None:
        from services.llm.service import get_llm_service
        _memory_agent = MemoryAgent(get_llm_service())
    return _memory_agent
