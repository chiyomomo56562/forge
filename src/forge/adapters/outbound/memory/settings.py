from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemorySettings:
    sqlite_path: Path
    chroma_path: Path
    collection_name: str = "episodes"
    projection_version: int = 1
    embedding_model_id: str = "default"
