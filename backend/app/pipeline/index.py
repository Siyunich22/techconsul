"""Чанкинг (≈800–1200 токенов с перекрытием) и гибридный поиск BM25 + вектор (TZ §5.1, §5.3)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.pipeline.ingest.base import Page


@dataclass
class ChunkDraft:
    text: str
    page: int | None
    page_end: int | None
    sheet: str | None = None
    cell_range: str | None = None

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.text)


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / get_settings().chars_per_token))


def _units(page: Page) -> list[str]:
    """Абзацы страницы; слишком длинные абзацы режем по предложениям."""
    s = get_settings()
    limit = int(s.chunk_target_tokens * s.chars_per_token)
    out: list[str] = []
    for para in page.text.split("\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            out.append(para)
    return out


def chunk_pages(pages: list[Page]) -> list[ChunkDraft]:
    s = get_settings()
    target = int(s.chunk_target_tokens * s.chars_per_token)
    overlap = int(s.chunk_overlap_tokens * s.chars_per_token)
    drafts: list[ChunkDraft] = []

    # листы Excel — блоками строк, источник = диапазон ячеек
    for page in pages:
        if page.rows is None:
            continue
        block: list[tuple[int, str]] = []
        size = 0
        header = f"Лист «{page.sheet}»"
        for r, line in page.rows:
            if block and size + len(line) > target:
                drafts.append(_sheet_chunk(page, header, block))
                block, size = block[-2:], sum(len(x) for _, x in block[-2:])  # перекрытие — 2 строки
            block.append((r, line))
            size += len(line) + 1
        if block:
            drafts.append(_sheet_chunk(page, header, block))

    # текстовые страницы — абзацами через границы страниц, с перекрытием
    buf: list[tuple[int, str]] = []  # (страница, абзац)
    size = 0
    for page in pages:
        if page.rows is not None:
            continue
        for unit in _units(page):
            if buf and size + len(unit) > target:
                drafts.append(_text_chunk(buf))
                tail, tail_size = [], 0
                for item in reversed(buf):
                    if tail_size + len(item[1]) > overlap:
                        break
                    tail.insert(0, item)
                    tail_size += len(item[1])
                buf, size = tail, tail_size
            buf.append((page.no, unit))
            size += len(unit) + 1
    if buf:
        drafts.append(_text_chunk(buf))
    return drafts


def _text_chunk(buf: list[tuple[int, str]]) -> ChunkDraft:
    return ChunkDraft(text="\n".join(u for _, u in buf), page=buf[0][0], page_end=buf[-1][0])


def _sheet_chunk(page: Page, header: str, block: list[tuple[int, str]]) -> ChunkDraft:
    cell_range = f"A{block[0][0]}:{page.max_col}{block[-1][0]}"
    return ChunkDraft(
        text=f"{header}, ячейки {cell_range}\n" + "\n".join(x for _, x in block),
        page=page.no,
        page_end=page.no,
        sheet=page.sheet,
        cell_range=cell_range,
    )


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    category: str | None
    page: int | None
    page_end: int | None
    sheet: str | None
    cell_range: str | None
    text: str
    score: float


_HYBRID_SQL = """
WITH fts AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC) AS rnk
    FROM chunks c, websearch_to_tsquery('russian', :q) q
    WHERE c.project_id = :project_id AND c.org_id = :org_id AND c.tsv @@ q {cat}
    ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT :pool
), vec AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> CAST(:emb AS vector)) AS rnk
    FROM chunks c
    WHERE c.project_id = :project_id AND c.org_id = :org_id AND c.embedding IS NOT NULL {cat}
    ORDER BY c.embedding <=> CAST(:emb AS vector) LIMIT :pool
), fused AS (
    SELECT id, sum(1.0 / (60 + rnk)) AS score
    FROM (SELECT * FROM fts UNION ALL SELECT * FROM vec) u GROUP BY id
)
SELECT c.id, c.document_id, d.filename, d.category, c.page, c.page_end, c.sheet, c.cell_range, c.text, f.score
FROM fused f JOIN chunks c ON c.id = f.id JOIN documents d ON d.id = c.document_id
WHERE d.deleted_at IS NULL
ORDER BY f.score DESC LIMIT :k
"""


def search_project(
    db: Session,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    query: str,
    k: int = 8,
    categories: list[str] | None = None,
) -> list[SearchHit]:
    """Гибридный поиск по документам проекта: полнотекст (russian) + косинус по эмбеддингам, слияние RRF."""
    from app.pipeline.embedder import get_embedder

    emb = get_embedder().embed_query(query)
    cat = "AND c.document_id IN (SELECT id FROM documents WHERE category = ANY(:cats))" if categories else ""
    params = {
        "q": query,
        "project_id": project_id,
        "org_id": org_id,
        "emb": "[" + ",".join(f"{x:.6f}" for x in emb) + "]",
        "pool": max(k * 5, 30),
        "k": k,
    }
    if categories:
        params["cats"] = categories
    rows = db.execute(sql(_HYBRID_SQL.format(cat=cat)), params).all()
    return [SearchHit(*row) for row in rows]
