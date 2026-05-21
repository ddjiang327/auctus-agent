"""Local document RAG using the existing Chroma instance.

Flow:
  index_folder(path) → chunk files → embed → upsert into rag_docs collection
  search(query, k)   → embed query → vector search → return top-k chunks
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from .config import settings


_COLLECTION_NAME = "rag_docs"
_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 100
_SUPPORTED_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".htm"}
_MAX_FILE_BYTES = 500_000


def _get_collection():
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(settings.data_dir / "chroma"))
        return client.get_or_create_collection(
            _COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception:
        return None


def _chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + _CHUNK_SIZE, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks


def _doc_id(folder: str, rel_path: str, chunk_idx: int) -> str:
    raw = f"{folder}|{rel_path}|{chunk_idx}"
    return hashlib.md5(raw.encode()).hexdigest()


def index_folder(folder_path: str) -> dict:
    """Index all supported text files in folder_path into Chroma."""
    col = _get_collection()
    if col is None:
        return {"ok": False, "error": "Chroma 不可用，请检查依赖安装"}

    folder = Path(folder_path).expanduser().resolve()
    if not folder.exists():
        return {"ok": False, "error": f"文件夹不存在：{folder}"}

    docs: list[str] = []
    ids: list[str] = []
    metas: list[dict] = []

    for fpath in sorted(folder.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() not in _SUPPORTED_EXTS:
            continue
        try:
            if fpath.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = str(fpath.relative_to(folder))
        for i, chunk in enumerate(_chunk_text(text)):
            docs.append(chunk)
            ids.append(_doc_id(str(folder), rel, i))
            metas.append({"folder": str(folder), "file": rel, "chunk": i})

    if not docs:
        return {"ok": True, "indexed": 0, "message": "未找到支持的文件（.txt .md .py 等）"}

    try:
        from . import llm
        vecs = llm.embed(docs)
    except Exception as exc:
        return {"ok": False, "error": f"Embedding 失败：{exc}（请确认已配置支持 embedding 的模型）"}

    batch = 100
    for i in range(0, len(docs), batch):
        col.upsert(
            ids=ids[i:i + batch],
            documents=docs[i:i + batch],
            embeddings=vecs[i:i + batch],
            metadatas=metas[i:i + batch],
        )

    _add_indexed_folder(str(folder))
    return {"ok": True, "indexed": len(docs)}


def search(query: str, k: int = 4) -> list[dict]:
    """Return top-k relevant chunks for the query."""
    col = _get_collection()
    if col is None:
        return []
    try:
        from . import llm
        vecs = llm.embed([query])
        results = col.query(query_embeddings=vecs, n_results=k)
        out: list[dict] = []
        docs = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        for doc, meta in zip(docs, metadatas):
            out.append({
                "content": doc,
                "file": (meta or {}).get("file", ""),
                "folder": (meta or {}).get("folder", ""),
            })
        return out
    except Exception:
        return []


def get_indexed_folders() -> list[str]:
    from . import accounting
    val = accounting.get_setup_state().get("rag_indexed_folders", "")
    if not val:
        return []
    try:
        return json.loads(val)
    except Exception:
        return []


def _add_indexed_folder(folder: str) -> None:
    from . import accounting
    folders = get_indexed_folders()
    if folder not in folders:
        folders.append(folder)
    accounting.set_setup_state({"rag_indexed_folders": json.dumps(folders)})


def remove_folder(folder: str) -> dict:
    col = _get_collection()
    folder_resolved = str(Path(folder).expanduser().resolve())
    if col is not None:
        try:
            results = col.get(where={"folder": folder_resolved})
            if results and results.get("ids"):
                col.delete(ids=results["ids"])
        except Exception:
            pass
    from . import accounting
    folders = [f for f in get_indexed_folders() if f != folder_resolved]
    accounting.set_setup_state({"rag_indexed_folders": json.dumps(folders)})
    return {"ok": True}
