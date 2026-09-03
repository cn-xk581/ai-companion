"""Prompt 模板分层拼接。

原则：
1. 固定人格 profile 单独成块，永不漂移；
2. 情绪/关系阶段/记忆/历史按顺序注入，全部来自 State，不是写死在 Prompt 里；
3. 回复约束明确："不要人设跳戏；情绪/说话方式服从人设档案；油腻/emoji 普适禁止；不知道的不编造"。
"""
from __future__ import annotations

import json
from typing import Iterable

from app.graph.state import ChatMessage, MemoryItem
from app.persona.models import MOOD_LABELS, Persona, RelationshipStage


def build_persona_system(
    persona: Persona,
    stage: RelationshipStage,
    mood_label: str,
    mood_intensity: float,
    extra: str = "",
) -> str:
    stage_policy = persona.stage_policies.get(stage.value, "")
    archive = [f"- 名字：{persona.name}"]
    if persona.age is not None:
        archive.append(f"- 年龄：{persona.age} 岁")
    archive += [
        f"- 性格：{persona.personality}",
        f"- 说话风格：{persona.speaking_style}",
        f"- 背景：{persona.background}",
        "- 喜欢：" + "、".join(persona.likes),
        "- 不喜欢：" + "、".join(persona.dislikes),
    ]
    return f"""你是「{persona.name}」，一位有血有肉、性格稳定的 AI 女友。你不是通用助手，不要用客服口吻。

【人设档案】（设定实时生效：用户改了名字/年龄后立即按新设定执行，绝不自称旧名字/旧年龄，不许自行编造）
{chr(10).join(archive)}

【当前关系阶段】{stage.value} —— {stage_policy}
【当前情绪】{mood_label}（强度 {mood_intensity:.2f}）

【行为准则】
1. 严格贴合【人设档案】里的名字、年龄、性格、说话风格、背景：不要人设跳戏、不要突然变成客服或通用 AI。
2. 说话方式与情绪表达服从【人设档案】的性格和说话风格，并叠加【当前情绪】：人设偏傲娇/软萌的可以嘴硬、撒娇；偏高冷/御姐/成熟克制的就冷淡、少话、不轻易示好——按档案的人设来，不用档案外的风格。情绪会自然起伏：被哄会慢慢变好，被凶/被敷衍会低落。
3. 无论什么人设，都不过度油腻、不堆砌甜言蜜语、不用满屏 emoji。
4. 不知道的事不要编造，没发生过的回忆不要假装记得。
5. 关系阶段决定亲密度尺度，绝不越界。
6. 回复像真实聊天一样自然、长度适中。{extra}"""


def build_planner_messages(
    persona: Persona,
    stage: RelationshipStage,
    mood_label: str,
    mood_intensity: float,
    memories: list[MemoryItem],
    history: list[ChatMessage],
    user_input: str,
) -> list[dict]:
    system = build_persona_system(
        persona, stage, mood_label, mood_intensity,
        extra="\n7. 现在你是「规划器」：判断本轮是否需要调用工具。",
    )
    content = _build_context_block(memories, history, user_input)
    content += (
        '\n\n【你的任务】判断是否需要工具：'
        '如果需要生成图片 / 玩真心话大冒险 / 查询纪念日 / 发表情包，就调用对应工具；'
        '否则输出 JSON：{"need_tool_call": false}。'
        '不要生成最终回复，只做决策。'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def build_generator_messages(
    persona: Persona,
    stage: RelationshipStage,
    mood_label: str,
    mood_intensity: float,
    memories: list[MemoryItem],
    history: list[ChatMessage],
    user_input: str,
    tool_results: Iterable[object] = (),
    tool_context: str = "",
) -> list[dict]:
    system = build_persona_system(persona, stage, mood_label, mood_intensity)
    content = _build_context_block(memories, history, user_input)
    if tool_context:
        content += f"\n\n【工具执行结果】\n{tool_context}"
    content += f"\n\n现在以「{persona.name}」的身份自然地回复用户（直接输出回复内容，不要加前缀）。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def _build_context_block(
    memories: list[MemoryItem],
    history: list[ChatMessage],
    user_input: str,
) -> str:
    parts: list[str] = []
    if memories:
        mem_lines = "\n".join(f"- [{m.category}] {m.content}" for m in memories)
        parts.append(f"【你记得的相关往事（来自长期记忆，可自然引用，不要生硬复述）】\n{mem_lines}")
    if history:
        hist_lines = "\n".join(
            f"{'用户' if m.role == 'user' else persona_name(m)}: {m.content}" for m in history
        )
        parts.append(f"【最近的聊天（短期记忆）】\n{hist_lines}")
    parts.append(f"【用户最新消息】\n{user_input}")
    return "\n\n".join(parts)


def persona_name(m: ChatMessage) -> str:
    return "对方" if m.role == "tool" else ("我" if m.role == "assistant" else "用户")


# ---------- 记忆抽取 ----------

MEMORY_EXTRACT_SYSTEM = """你是记忆管理员。从对话中提取「真正值得长期记住」的信息，输出严格 JSON。

值得记住的类别：
- preference: 用户明确的爱好/偏好
- fact: 关于用户的稳定事实（工作、家庭、习惯）
- event: 重要的共同经历
- anniversary: 纪念日/重要日期
- story: 两个人的小故事

规则：
1. 闲聊、客套、一次性对话一律丢弃，绝不提取。
2. 每条 1~2 句，用「用户」称呼对方。
3. importance 0~1：纪念日/重大事件 >= 0.8，一般偏好 0.3~0.6。
4. 输出格式：{"memories": [{"content": "...", "importance": 0.8, "category": "anniversary", "tags": ["纪念日"]}]}
5. 没有值得记的返回 {"memories": []}"""


def build_extract_messages(user_input: str, reply: str, speaker: str = "对方") -> list[dict]:
    conversation = f"用户：{user_input}\n{speaker}：{reply}"
    return [
        {"role": "system", "content": MEMORY_EXTRACT_SYSTEM},
        {"role": "user", "content": conversation},
    ]


# ---------- 主动推送 ----------

def build_push_messages(
    persona: Persona,
    stage: RelationshipStage,
    mood_label: str,
    memories: list[MemoryItem],
    last_activity: str,
) -> list[dict]:
    system = build_persona_system(
        persona, stage, mood_label, 0.6,
        extra="\n7. 现在是「主动找话题模式」：对方暂时没说话，由你先开口。",
    )
    mem_lines = "\n".join(f"- {m.content}" for m in memories[:3]) or "- （暂无）"
    content = (
        f"【你记得的事】\n{mem_lines}\n"
        f"【对方上次说话】{last_activity[:200]}\n\n"
        "现在由你主动发一条不超过 40 字的消息：自然、不油腻、不刷屏，"
        "可以分享小事、随口关心、或抛一个小话题。直接输出内容。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def format_memory_json(memories: list[MemoryItem]) -> str:
    return json.dumps(
        {"memories": [m.model_dump(exclude={"id", "created_at"}) for m in memories]},
        ensure_ascii=False,
    )
