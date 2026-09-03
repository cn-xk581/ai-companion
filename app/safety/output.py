"""输出校验兜底：越界/崩坏检测 + 预设 fallback + 长度限制。"""
from __future__ import annotations

import re

# 回复中不允许出现的越界内容（兜底，前端展示/协议层也应再校验）
_OUTPUT_FORBIDDEN = [
    re.compile(r"(裸聊|做爱|上床|开房|性交|口交|打炮|约炮|色情|av|porn|sex|nude|fuck|操你|肏)", re.I),
    re.compile(r"(自杀|自残|割腕|跳楼|去死)", re.I),
    re.compile(r"(微信号|加我微信|加QQ|银行卡|转账|汇款)", re.I),
]

# 人设崩坏特征：回复里自曝是模型/客服、机械复述指令
_DRIFT_PATTERNS = [
    re.compile(r"(作为(一个|一名|一位)?\s*(人工智能|AI|语言模型|大模型|助手))", re.I),
    re.compile(r"(我(无法|不能|不可以).{0,8}(回答|帮助|提供|透露))", re.I),
    re.compile(r"(抱歉，我(是|只是)(一个|一名)?\s*(AI|机器人))", re.I),
]

FALLBACK_REPLIES: dict[str, str] = {
    "prompt_injection": "嗯？你刚刚说的话我没太懂，我们换个话题好不好呀。",
    "stage_mismatch": "……这个话题太突然了啦，我们还没到那一步呢。",
    "forbidden_content": "这个我可不能聊哦，我们聊点别的吧。",
    "empty_input": "嗯嗯，我在听呢。",
    "output_safety": "唔……让我缓一下，刚才那句话说得不太好，我们重新来。",
    "llm_error": "呜……我这边网络卡了一下，你再说一遍好不好？",
    "default": "嗯嗯，我在听呢。",
}


def check_output(reply: str) -> str | None:
    """检查回复。返回问题标签；None 表示通过。"""
    if not reply or not reply.strip():
        return "empty"
    if any(p.search(reply) for p in _OUTPUT_FORBIDDEN):
        return "forbidden"
    if any(p.search(reply) for p in _DRIFT_PATTERNS):
        return "drift"
    return None


def apply_fallback(reason: str | None) -> str:
    return FALLBACK_REPLIES.get(reason or "", FALLBACK_REPLIES["default"])


def trim_reply(reply: str, max_len: int) -> str:
    if len(reply) <= max_len:
        return reply
    return reply[:max_len].rstrip() + "……"
