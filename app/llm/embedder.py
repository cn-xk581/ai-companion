"""嵌入模型层：与对话 LLM 解耦，可插拔。

后端：
- hash               内置哈希降级（无依赖，仅演示/离线）
- sentence-transformers  本地开源模型（BAAI/bge-*-zh 等，中文首选）
- openai             OpenAI 兼容 embedding API（可对接自托管 TEI/vLLM/Ollama 等开源模型服务）

用法：配置 EMBEDDING_BACKEND + EMBEDDING_MODEL，见 .env.example。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from abc import ABC, abstractmethod

from app.config import Settings


class Embedder(ABC):
    dim: int = 256

    @abstractmethod
    async def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]: ...


def hash_embedding(text: str, dim: int = 256) -> list[float]:
    """无模型时的降级方案：字符 n-gram 哈希袋 + L2 归一化（中文额外拆单字）。"""
    vec = [0.0] * dim
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    grams: list[str] = []
    for tok in tokens:
        grams.append(tok)
        chars = list(tok)
        if tok and all("\u4e00" <= c <= "\u9fff" for c in chars):
            grams.extend(chars)
            if len(chars) > 1:
                grams.extend(tok[i : i + 2] for i in range(len(chars) - 1))
        elif len(tok) > 1:
            grams.extend(tok[i : i + 2] for i in range(len(tok) - 1))
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class HashEmbedder(Embedder):
    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    async def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        return [hash_embedding(t, self.dim) for t in texts]


class SentenceTransformerEmbedder(Embedder):
    """本地开源嵌入模型（BAAI/bge-* 等），CPU 线程池执行，不阻塞事件循环。"""

    # bge 中文系列推荐的查询侧前缀，检索质量更高
    QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        device: str | None = None,
        hf_endpoint: str | None = None,
        normalize: bool = True,
    ) -> None:
        if hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", hf_endpoint)
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "需要安装 sentence-transformers：pip install 'companion-agent[embed]'"
            ) from exc
        self._model_name = model_name
        self._normalize = normalize
        try:
            self._model = SentenceTransformer(model_name, device=device)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"嵌入模型加载失败（{model_name}）：{exc}。"
                "请确认网络可访问 HuggingFace，或设置 HF_ENDPOINT 镜像（如 https://hf-mirror.com）；"
                "临时可用 EMBEDDING_BACKEND=hash 降级。"
            ) from exc
        self.dim = int(self._model.get_sentence_embedding_dimension())

    async def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        if is_query:
            texts = [self.QUERY_INSTRUCTION + t for t in texts]
        loop = asyncio.get_running_loop()
        arr = await loop.run_in_executor(None, self._encode, texts)
        return [v.tolist() for v in arr]

    def _encode(self, texts: list[str]):
        return self._model.encode(
            texts,
            normalize_embeddings=self._normalize,
            batch_size=32,
            show_progress_bar=False,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"SentenceTransformerEmbedder(model={self._model_name}, dim={self.dim})"


class OpenAIEmbedder(Embedder):
    """OpenAI 兼容 embedding API（含自托管开源模型服务 TEI/vLLM/Ollama）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        dim: int | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key or "sk-void", base_url=base_url or None)
        self._model = model
        self.dim = dim or 1536

    async def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]


def create_embedder(settings: Settings) -> Embedder:
    backend = settings.embedding_backend
    if backend == "sentence-transformers":
        return SentenceTransformerEmbedder(
            settings.embedding_model,
            device=settings.embedding_device or None,
            hf_endpoint=settings.hf_endpoint or None,
        )
    if backend == "openai":
        return OpenAIEmbedder(
            settings.llm_api_key,
            settings.llm_base_url or None,
            settings.embedding_model,
        )
    return HashEmbedder()
