"""ORM-модели. Каждый модуль с моделями импортируется здесь, чтобы Alembic видел их в Base.metadata."""

from app.models.document import (
    CategorySource,
    Chunk,
    Document,
    DocumentPage,
    DocumentStatus,
    LlmCall,
    ReferenceChunk,
    ReferenceDoc,
    ReferenceKind,
    UploadSession,
)
from app.models.org import (
    AuditLog,
    Expert,
    Organization,
    RefreshSession,
    TokenPurpose,
    User,
    UserRole,
    UserToken,
)
from app.models.project import MemberRole, Project, ProjectMember, ProjectStatus, TemplateVersion

__all__ = [
    "AuditLog",
    "CategorySource",
    "Chunk",
    "Document",
    "DocumentPage",
    "DocumentStatus",
    "LlmCall",
    "ReferenceChunk",
    "ReferenceDoc",
    "ReferenceKind",
    "UploadSession",
    "Expert",
    "MemberRole",
    "Organization",
    "Project",
    "ProjectMember",
    "ProjectStatus",
    "RefreshSession",
    "TemplateVersion",
    "TokenPurpose",
    "User",
    "UserRole",
    "UserToken",
]
