"""Справочная библиотека (НДТ, кодексы, СН РК): разбор и индекс в reference_chunks — тот же ingest."""

import logging
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.core.storage import Storage
from app.models import DocumentStatus, ReferenceChunk, ReferenceDoc
from app.pipeline.embedder import get_embedder
from app.pipeline.index import chunk_pages
from app.pipeline.ingest import parse_file
from app.pipeline.ingest.base import IngestError, Page

log = logging.getLogger(__name__)


def ingest_reference(db: Session, storage: Storage, ref_id: uuid.UUID) -> None:
    """Разбор и индекс за один шаг: задача идёт в очередь index, где модель эмбеддингов уже загружена."""
    ref = db.get(ReferenceDoc, ref_id)
    if ref is None:
        return
    ref.status, ref.error = DocumentStatus.processing, None
    db.commit()
    try:
        with tempfile.TemporaryDirectory(prefix="ref_") as tmp:
            src = Path(tmp) / f"source{Path(ref.filename).suffix.lower()}"
            storage.download(ref.file_key, src)
            parsed = parse_file(src, ref.filename, Path(tmp))
        if parsed.is_container:
            raise IngestError(
                "Архивы в справочной библиотеке не поддерживаются — загрузите документы по одному"
            )
        ref.pages = len(parsed.pages)
        _index(db, ref, parsed.pages)
    except IngestError as exc:
        ref.status, ref.error = DocumentStatus.error, str(exc)
        db.commit()
    except Exception as exc:
        log.exception("Ошибка обработки справочника %s", ref_id)
        db.rollback()
        ref.status, ref.error = DocumentStatus.error, f"Внутренняя ошибка: {exc.__class__.__name__}: {exc}"
        db.commit()


def _index(db: Session, ref: ReferenceDoc, pages: list[Page]) -> None:
    drafts = chunk_pages(pages)
    embedder = get_embedder()
    db.execute(delete(ReferenceChunk).where(ReferenceChunk.reference_doc_id == ref.id))
    for start in range(0, len(drafts), 32):
        batch = drafts[start : start + 32]
        for i, (d, vec) in enumerate(
            zip(batch, embedder.embed_passages([d.text for d in batch]), strict=True)
        ):
            db.add(
                ReferenceChunk(
                    reference_doc_id=ref.id,
                    seq=start + i,
                    page=d.page,
                    page_end=d.page_end,
                    text=d.text,
                    token_count=d.token_count,
                    embedding=vec,
                )
            )
    ref.chunk_count = len(drafts)
    ref.status = DocumentStatus.indexed
    db.commit()


_REF_SQL = """
WITH fts AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC) AS rnk
    FROM reference_chunks c, websearch_to_tsquery('russian', :q) q WHERE c.tsv @@ q {kind}
    ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT :pool
), vec AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> CAST(:emb AS vector)) AS rnk
    FROM reference_chunks c WHERE c.embedding IS NOT NULL {kind}
    ORDER BY c.embedding <=> CAST(:emb AS vector) LIMIT :pool
), fused AS (
    SELECT id, sum(1.0 / (60 + rnk)) AS score
    FROM (SELECT * FROM fts UNION ALL SELECT * FROM vec) u GROUP BY id
)
SELECT c.id, r.id, r.title, r.kind, c.page, c.page_end, c.text, f.score
FROM fused f JOIN reference_chunks c ON c.id = f.id JOIN reference_docs r ON r.id = c.reference_doc_id
ORDER BY f.score DESC LIMIT :k
"""


def search_references(db: Session, query: str, k: int = 8, kinds: list[str] | None = None) -> list[dict]:
    emb = get_embedder().embed_query(query)
    kind = (
        "AND c.reference_doc_id IN (SELECT id FROM reference_docs WHERE kind::text = ANY(:kinds))"
        if kinds
        else ""
    )
    params = {
        "q": query,
        "emb": "[" + ",".join(f"{x:.6f}" for x in emb) + "]",
        "pool": max(k * 5, 30),
        "k": k,
    }
    if kinds:
        params["kinds"] = kinds
    rows = db.execute(sql(_REF_SQL.format(kind=kind)), params).all()
    keys = ("chunk_id", "reference_doc_id", "title", "kind", "page", "page_end", "text", "score")
    return [dict(zip(keys, row, strict=True)) for row in rows]
