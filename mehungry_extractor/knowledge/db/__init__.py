"""SQLite persistence for the deterministic engine (spec §13).

Phase 1 tables only: documents, document_versions, sections, paragraphs, sentences,
extraction_runs. Later phases add their own tables. We use ``Base.metadata.create_all``
guarded by ``SCHEMA_VERSION`` rather than Alembic until the schema stabilizes.
"""

from .schema import (
    Base,
    DocumentRow,
    DocumentVersionRow,
    ExtractionRunRow,
    ParagraphRow,
    SectionRow,
    SentenceRow,
    load_document,
    persist_document,
)
from .session import default_db_path, get_engine, init_db, session_scope

__all__ = [
    "Base",
    "DocumentRow",
    "DocumentVersionRow",
    "ExtractionRunRow",
    "ParagraphRow",
    "SectionRow",
    "SentenceRow",
    "load_document",
    "persist_document",
    "default_db_path",
    "get_engine",
    "init_db",
    "session_scope",
]
