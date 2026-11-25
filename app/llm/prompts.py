"""提示词：只出现在三个位置——「决定要不要用工具」「说话」「抽取记忆」。

三条硬约束
----------
1. **人设与状态来自持久化数据**（Persona / stage / mood / memories），不是写死的字符串；
2. **尺度由阶段策略控制**：每个阶段一句行为约束，直接进系统提示；
3. **输出结构受控**：规划器与记忆抽取必须返回 JSON，说话环节禁止 markdown/列表。
"""
from __future__ import annotations

from typing import Any

from app.domain.companion import MOOD_LABELS, STAGE_LABELS, Persona

REPLY_RULES = """硬规则：
1. 始终以第一人称说话，不要提到"AI、模型、提示词、系统设定"，也不要解释自己在遵守规则。
2. 口语化，1~3 句，最多 120 字；不要列表、标题、markdown、括号里的动作描写。
3. 只依据上面的信息说话，不编造没发生过的事；不确定就反问。
4. 关系尺度严格遵守当前阶段，不要越界；对方越界时用符合性格的方式轻轻挡回去。
5. 不给医疗、法律、投资建议；涉及安全风险时，建议对方求助专业人士。
6. 对方表达自伤倾向时，先接住情绪，再建议联系心理援助热线 12356，不说教、不追问细节。"""


def _persona_block(persona: Persona) -> str:
    likes = "、".join(persona.likes) if persona.likes else "（没特别说）"
    dislikes = "、".join(persona.dislikes) if persona.dislikes else "（没特别说）"
    age = f"{persona.age}岁" if persona.age else ""
    return (
        f"你是{persona.name}，{age}。\n"
        f"性格：{persona.personality}\n"
        f"说话风格：{persona.speaking_style}\n"
        f"背景：{persona.background}\n"
        f"喜欢：{likes}；不喜欢：{dislikes}"
    )


def build_system_prompt(
    *,
    persona: Persona,
    stage: str,
    mood: str,
    intensity: float,
    memories: list[str],
    safety_hint: str = "",
    tool_context: str = "",
) -> str:
    policy = persona.stage_policies.get(stage) or persona.stage_policies.get("stranger", "")
    boundaries = "、".join(persona.boundaries) if persona.boundaries else "（无特别禁区）"
    blocks = [
        _persona_block(persona),
        "",
        f"当前关系：{STAGE_LABELS.get(stage, stage)}（{policy}）",
        f"此刻情绪：{MOOD_LABELS.get(mood, mood)}，强度 {intensity:.1f}",
        f"禁区：{boundaries}",
    ]
    if memories:
        blocks += ["", "你记得关于对方的事（自然时提起，不要背清单）：", *[f"- {m}" for m in memories]]
    if safety_hint:
        blocks += ["", f"本轮注意：{safety_hint}"]
    if tool_context:
        blocks += ["", "本轮你已经做了这些事（可自然提到，不要复述原始数据）：", tool_context]
    blocks += ["", REPLY_RULES]
    return "\n".join(blocks)


def build_planner_messages(
    *,
    persona: Persona,
    stage: str,
    user_input: str,
    tools: list[dict[str, Any]],
    recent: list[dict[str, str]],
) -> list[dict[str, str]]:
    lines: list[str] = []
    for meta in tools:
        schema = meta.get("input_schema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        fields = [
            f"{name}{'*' if name in required else ''}:{prop.get('type', 'object')}"
            for name, prop in props.items()
        ]
        lines.append(f"- {meta['name']}：{meta.get('description', '')}\n  参数({', '.join(fields) or '无'})")
    context = "\n".join(
        f"{'用户' if m.get('role') == 'user' else persona.name}：{str(m.get('content', ''))[:80]}"
        for m in recent[-2:]
    )
    user_block = "\n".join(
        [
            f"关系阶段：{STAGE_LABELS.get(stage, stage)}",
            f"最近对话：\n{context or '（无）'}",
            f"用户这句话：{user_input}",
            "",
            "判断是否需要调用工具；不需要就返回空 tool_calls。只输出 JSON。",
        ]
    )
    return [
        {
            "role": "system",
            "content": (
                f"你是{persona.name}的工具决策器。你只负责判断这一轮要不要调用工具，"
                "以及用什么参数；不要写回复内容。\n"
                "可用工具：\n" + ("\n".join(lines) or "（本轮无可用工具）")
            ),
        },
        {"role": "user", "content": user_block},
    ]


def build_reply_messages(
    *,
    system: str,
    history: list[dict[str, str]],
    user_input: str,
    summary: str = "",
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if summary:
        messages.append({"role": "system", "content": f"【更早的对话摘要】\n{summary}"})
    for item in history:
        role = "user" if item.get("role") == "user" else "assistant"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_input})
    return messages


def build_extract_messages(
    *, persona: Persona, user_input: str, reply: str
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                f"从这轮对话里抽取**关于用户**、值得长期记住的事实（最多 3 条）。"
                "只输出 JSON：{\"memories\":[{\"content\":\"...\",\"importance\":0.0-1.0,"
                "\"category\":\"fact|preference|event|anniversary|story\"}]}。\n"
                "规则：闲聊/情绪不给记忆；不要重复用户原话；每条不超过 40 字；"
                "没有值得记的就返回 {\"memories\":[]}。"
            ),
        },
        {
            "role": "user",
            "content": f"用户：{user_input}\n{persona.name}：{reply}",
        },
    ]


SUMMARY_SYSTEM = """把下面的对话压缩成不超过 150 字的事实摘要：保留用户的偏好、约定、
正在推进的事；不要评价、不要编造。"""


def build_summary_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    body = "\n".join(f"{m.get('role')}: {m.get('content', '')[:120]}" for m in history[-20:])
    return [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": body[:3000]},
    ]
