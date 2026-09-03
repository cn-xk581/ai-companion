"""generate_image：调用多模态层生成情侣 / 人设图片。"""
from __future__ import annotations

from app.graph.context import GraphContext

SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": "生成一张图片（情侣合影、人设形象、风景等）。用户要求画图/生成图片/拍照时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "图片内容描述，中文，具体到人物动作、场景、氛围"},
                "style": {"type": "string", "description": "风格：写实 / 二次元 / 插画 / 水彩，可选"},
            },
            "required": ["prompt"],
        },
    },
}


async def handler(args: dict, ctx: GraphContext, session_id: str) -> str:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "图片生成失败：缺少画面描述。"
    result = await ctx.llm.generate_image(prompt, args.get("style"))
    if result.error:
        return f"图片生成失败了：{result.error}"
    if result.url:
        return f"画好啦～给你一张图：{result.url}"
    return "图片生成服务暂时不可用，先欠你一张哦。"
