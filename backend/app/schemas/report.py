import uuid
from datetime import datetime

from app.schemas.common import ORMModel


class ReportOut(ORMModel):
    id: uuid.UUID
    number: int
    version: str
    status: str
    render_status: str
    error: str | None
    filename: str
    has_docx: bool
    has_pdf: bool
    sha256: str | None
    created_at: datetime
    finished_at: datetime | None
