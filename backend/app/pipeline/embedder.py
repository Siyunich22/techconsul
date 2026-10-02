"""Эмбеддинги за интерфейсом Embedder (TZ §3).

По умолчанию intfloat/multilingual-e5-large через fastembed (ONNX)."""

import hashlib
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _flat_model_dir(model: str) -> Path:
    """Модель в отдельном каталоге с настоящими файлами (не символическими ссылками кэша HF).

    onnxruntime ≥1.2x проверяет, что внешние веса (model.onnx_data) лежат рядом с model.onnx;
    кэш Hugging Face хранит их ссылками в общий каталог blobs, и загрузка падает.
    """
    from fastembed import TextEmbedding
    from huggingface_hub import snapshot_download

    info = next(m for m in TextEmbedding.list_supported_models() if m["model"] == model)
    repo = info["sources"]["hf"]
    target = Path(os.environ.get("FASTEMBED_CACHE_PATH", "/models")) / "flat" / repo.replace("/", "__")
    files = [info["model_file"], *info.get("additional_files", [])]
    if not all((target / f).exists() for f in files):
        snapshot_download(repo_id=repo, local_dir=target)
    return target


class FastEmbedder:
    """E5 требует префиксов «passage: » / «query: » — добавляем явно."""

    def __init__(self, model: str, dim: int) -> None:
        from fastembed import TextEmbedding

        self.dim = dim
        self._model = TextEmbedding(model_name=model, specific_model_path=str(_flat_model_dir(model)))

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed([f"passage: {t}" for t in texts], batch_size=16)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.embed([f"query: {text}"]))).tolist()


class HashEmbedder:
    """Детерминированный «мешок слов» в хэш-пространстве — для тестов и офлайн-разработки."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"\w{3,}", text.lower()):
            h = int.from_bytes(hashlib.md5(word[:6].encode()).digest()[:4], "little")
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedder == "hash":
        return HashEmbedder(s.embedding_dim)
    return FastEmbedder(s.embedding_model, s.embedding_dim)
