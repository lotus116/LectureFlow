"""Local course material extraction and a small, inspectable terminology index."""

from __future__ import annotations

import json
import re
import shutil
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .files import metadata_edit
from .library import identity, now
from .runtime import sha256, write_json


def migrate(db) -> None:
    from .knowledge import migrate as migrate_knowledge

    migrate_knowledge(db)
    db.execute("""CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL, filename TEXT NOT NULL,
        original_name TEXT NOT NULL, sha256 TEXT NOT NULL,
        pages TEXT NOT NULL, created_at TEXT NOT NULL,
        removed INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE TABLE IF NOT EXISTS terms (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL, english TEXT NOT NULL,
        chinese TEXT NOT NULL, confirmed INTEGER NOT NULL, sources TEXT NOT NULL,
        UNIQUE(kind,owner_id,english))""")


def owner_path(library, kind: str, key: str) -> Path:
    from .files import hidden, path

    if kind not in {"course", "lecture"} or hidden(library, kind, key):
        raise ValueError("请选择尚未删除的课程或课堂")
    if kind == "lecture" and hidden(library, "course", library.row("lectures", key)["course_id"]):
        raise ValueError("所属课程在废纸篓中，请先恢复")
    return library.managed_path(path(library, kind, key) / "Documents")


def invalidate(library, kind: str, key: str) -> None:
    column = "course_id" if kind == "course" else "id"
    library.db.execute(f"UPDATE lectures SET revision=revision+1 WHERE {column}=?", (key,))


def _xml(archive: zipfile.ZipFile, name: str):
    if archive.getinfo(name).file_size > 10_000_000:
        raise ValueError("课件单页文本数据过大，请拆分课件后导入")
    return ET.fromstring(archive.read(name))


def _pptx(source: Path) -> list[dict]:
    with zipfile.ZipFile(source) as archive:
        ns = {
            "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }
        relationships = {
            entry.attrib["Id"]: entry.attrib["Target"]
            for entry in _xml(archive, "ppt/_rels/presentation.xml.rels")
            if entry.attrib.get("TargetMode") != "External"
        }
        slides = _xml(archive, "ppt/presentation.xml").findall("p:sldIdLst/p:sldId", ns)
        pages = []
        for index, slide in enumerate(slides[:1000]):
            target = relationships[slide.attrib["{" + ns["r"] + "}id"]]
            name = target.lstrip("/") if target.startswith("/") else "ppt/" + target
            body = _xml(archive, name)
            paragraphs = [
                "".join(node.itertext()) for node in body.iter() if node.tag.endswith("}p")
            ]
            if not paragraphs:
                paragraphs = [
                    "".join(node.itertext()) for node in body.iter() if node.tag.endswith("}t")
                ]
            pages.append({"page": index + 1, "text": "\n".join(paragraphs), "method": "text"})
        if len(slides) > 1000:
            raise ValueError("课件超过 1000 页，请拆分导入")
        return pages


def legacy_records(data: bytes, depth: int = 0) -> list[str]:
    """MS-PPT records: UTF-16 TextCharsAtom / byte TextBytesAtom, bounded recursion."""
    if depth > 30:
        raise ValueError("PPT 记录嵌套过深")
    output, offset = [], 0
    while offset + 8 <= len(data):
        options, record_type, length = struct.unpack_from("<HHI", data, offset)
        offset += 8
        if length > len(data) - offset:
            break
        payload = data[offset : offset + length]
        offset += length
        if record_type in {4000, 4008}:
            output.append(
                payload.decode(
                    "utf-16-le" if record_type == 4000 else "cp1252", errors="replace"
                ).replace("\r", "\n")
            )
        elif options & 15 == 15:
            output.extend(legacy_records(payload, depth + 1))
    return output


def extract(source: Path) -> list[dict]:
    if source.stat().st_size > 250_000_000:
        raise ValueError("单个课件超过 250 MB，请拆分后导入")
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader, filters

        filters.ZLIB_MAX_OUTPUT_LENGTH = 20_000_000
        reader = PdfReader(source)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 有密码，请先解锁后导入")
        if len(reader.pages) > 1000:
            raise ValueError("课件超过 1000 页，请拆分导入")
        return [
            {"page": index + 1, "text": page.extract_text() or "", "method": "text"}
            for index, page in enumerate(reader.pages)
        ]
    if suffix == ".pptx":
        return _pptx(source)
    if suffix == ".ppt":
        import olefile

        with olefile.OleFileIO(source) as deck:
            data = deck.openstream("PowerPoint Document").read(100_000_001)
            if len(data) > 100_000_000:
                raise ValueError("PPT 文本流过大，请另存为 PPTX 后导入")
        return [{"page": 1, "text": "\n".join(legacy_records(data)), "method": "legacy-records"}]
    raise ValueError("支持 PDF、PPTX 和 PPT 课件")


STOP = set(
    "the this that these those and for from with will have your you our into what when where which "
    "can how introduction contents learning objectives slide page example examples thank summary "
    "university lecture chapter course part overview references information computer".split()
)


def candidates(pages: list[dict], document_id: str) -> list[dict]:
    from .knowledge import analyze

    return analyze(pages, document_id)["terms"]


@metadata_edit
def import_document(
    library, kind: str, owner_id: str, source: Path, *, extracted: dict | None = None
) -> dict:
    source = source.resolve()
    if not source.is_file() or source.suffix.lower() not in {".pdf", ".ppt", ".pptx"}:
        raise ValueError("请选择 PDF、PPTX 或 PPT 课件")
    directory = owner_path(library, kind, owner_id)
    directory.mkdir(parents=True, exist_ok=True)
    key = identity()
    folder = directory / key
    folder.mkdir()
    target = folder / ("source" + source.suffix.lower())
    try:
        shutil.copy2(source, target)
        digest = sha256(target)
        if digest != sha256(source) or (extracted and extracted.get("sha256") != digest):
            raise ValueError("课件在解析期间发生变化，请重新导入")
        pages = extracted["pages"] if extracted else extract(target)
        if (
            not isinstance(pages, list)
            or len(pages) > 1000
            or any(
                not isinstance(p.get("text"), str) or not isinstance(p.get("page"), int)
                for p in pages
            )
            or sum(len(p["text"]) for p in pages) > 2_000_000
        ):
            raise ValueError("提取的课件文本过大或格式有误")
        if not any(p["text"].strip() for p in pages):
            raise ValueError(
                "未找到文字。扫描 PDF 请通过应用导入以启用本地 OCR；"
                "图片型 PPT 请另存为 PDF 后导入。"
            )
        write_json(folder / "pages.json", {"sha256": digest, "pages": pages})
        terms = candidates(pages, key)
        with library.transaction():
            library.db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,0)",
                (
                    key,
                    kind,
                    owner_id,
                    str(target.relative_to(directory)),
                    source.name,
                    digest,
                    json.dumps(pages, ensure_ascii=False),
                    now(),
                ),
            )
            for term in terms:
                old = library.db.execute(
                    "SELECT * FROM terms WHERE kind=? AND owner_id=? AND english=?",
                    (kind, owner_id, term["english"]),
                ).fetchone()
                if old:
                    combined = json.loads(old["sources"]) + term["sources"]
                    library.db.execute(
                        "UPDATE terms SET sources=? WHERE id=?",
                        (json.dumps(combined[:40]), old["id"]),
                    )
                else:
                    library.db.execute(
                        "INSERT INTO terms VALUES(?,?,?,?,?,0,?)",
                        (
                            identity(),
                            kind,
                            owner_id,
                            term["english"],
                            term["chinese"],
                            json.dumps(term["sources"]),
                        ),
                    )
            from .knowledge import enqueue, index_document

            index_document(library, key, pages)
            enqueue(library, kind, owner_id)
            invalidate(library, kind, owner_id)
    except BaseException:
        shutil.rmtree(folder)
        raise
    return {"id": key, "pages": len(pages), "terms": len(terms)}


def listing(library, kind: str, owner_id: str, *, include_shared=False) -> dict:
    from . import knowledge

    directory = owner_path(library, kind, owner_id)
    knowledge.ensure_indexes(library, kind, owner_id)
    documents = []
    for row in library.db.execute(
        "SELECT * FROM documents WHERE kind=? AND owner_id=? AND removed=0 ORDER BY created_at",
        (kind, owner_id),
    ):
        item = dict(row)
        pages = json.loads(item.pop("pages"))
        item["page_count"] = len(pages)
        item["path"] = str(library.managed_path(directory / item["filename"]))
        item["preview"] = "\n\n".join(f"[{p['page']}] {p['text']}" for p in pages)[:100_000]
        index = library.db.execute(
            "SELECT outline FROM knowledge_indexes WHERE document_id=?", (item["id"],)
        ).fetchone()
        item["outline"] = json.loads(index[0]) if index else []
        documents.append(item)
    result = {
        "documents": documents,
        "terms": knowledge.terms(library, kind, owner_id),
        "jobs": knowledge.jobs(library, kind, owner_id),
    }
    if include_shared and kind == "lecture":
        shared = listing(library, "course", library.row("lectures", owner_id)["course_id"])
        for key in ("documents", "terms", "jobs"):
            result[key] = shared[key] + result[key]
    return result


@metadata_edit
def save_term(
    library,
    kind: str,
    owner_id: str,
    english: str,
    chinese: str,
    confirmed: bool,
    key: str | None = None,
) -> dict:
    owner_path(library, kind, owner_id)
    english, chinese = english.strip(), chinese.strip()
    if not english or len(english) > 160 or len(chinese) > 300:
        raise ValueError("请输入简短有效的术语")
    with library.transaction():
        if key:
            if not library.db.execute(
                "SELECT 1 FROM terms WHERE id=? AND kind=? AND owner_id=?", (key, kind, owner_id)
            ).fetchone():
                raise ValueError("找不到术语")
            library.db.execute(
                "UPDATE terms SET english=?,chinese=?,confirmed=? WHERE id=?",
                (english, chinese, int(confirmed), key),
            )
        else:
            key = identity()
            library.db.execute(
                "INSERT INTO terms VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,owner_id,english) "
                "DO UPDATE SET chinese=excluded.chinese,confirmed=excluded.confirmed",
                (key, kind, owner_id, english, chinese, int(confirmed), "[]"),
            )
        key = library.db.execute(
            "SELECT id FROM terms WHERE kind=? AND owner_id=? AND english=?",
            (kind, owner_id, english),
        ).fetchone()[0]
        library.db.execute("INSERT OR IGNORE INTO term_metadata(term_id) VALUES(?)", (key,))
        library.db.execute(
            "UPDATE term_metadata SET origin='manual',excluded=0 WHERE term_id=?", (key,)
        )
        invalidate(library, kind, owner_id)
    return {"id": key}


@metadata_edit
def remove(library, document_id: str, *, confirmed: bool = False) -> None:
    if not confirmed:
        raise ValueError("移除课件需要明确确认")
    row = library.db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if row is None:
        raise ValueError("找不到课件")
    # Keep its managed file and extraction as historical evidence. It no longer feeds new jobs.
    with library.transaction():
        library.db.execute("UPDATE documents SET removed=1 WHERE id=?", (document_id,))
        for term in library.db.execute(
            "SELECT * FROM terms WHERE kind=? AND owner_id=?", (row["kind"], row["owner_id"])
        ):
            sources = json.loads(term["sources"])
            remaining = [s for s in sources if s["document_id"] != document_id]
            if sources and not remaining:
                library.db.execute(
                    "UPDATE terms SET confirmed=0,sources='[]' WHERE id=?", (term["id"],)
                )
            elif sources != remaining:
                library.db.execute(
                    "UPDATE terms SET sources=? WHERE id=?", (json.dumps(remaining), term["id"])
                )
        invalidate(library, row["kind"], row["owner_id"])


def context(library, lecture_id: str) -> dict:
    from . import knowledge

    lecture = library.row("lectures", lecture_id)
    terms, documents, hint_terms = {}, [], {}
    for kind, owner_id in (("course", lecture["course_id"]), ("lecture", lecture_id)):
        knowledge.ensure_indexes(library, kind, owner_id)
        active = {
            r[0]
            for r in library.db.execute(
                "SELECT id FROM documents WHERE kind=? AND owner_id=? AND removed=0",
                (kind, owner_id),
            )
        }
        for row in knowledge.terms(library, kind, owner_id):
            valid_source = any(s["document_id"] in active for s in json.loads(row["sources"]))
            key = row["english"].casefold()
            if row["excluded"]:
                hint_terms.pop(key, None)
                terms.pop(key, None)
                continue
            if row["confirmed"] or (valid_source and row["score"] >= 4):
                hint_terms[key] = {
                    "english": row["english"],
                    "score": row["score"],
                    "confirmed": bool(row["confirmed"]),
                    "sources": json.loads(row["sources"]),
                }
            if row["confirmed"] or (valid_source and row["origin"] == "courseware"):
                terms[key] = (row["english"], row["chinese"])
        for row in library.db.execute(
            "SELECT id,original_name,pages FROM documents "
            "WHERE kind=? AND owner_id=? AND removed=0 ORDER BY created_at",
            (kind, owner_id),
        ):
            documents.append(
                {"id": row["id"], "name": row["original_name"], "pages": json.loads(row["pages"])}
            )
    snapshot = {
        "hint_terms": list(hint_terms.values())[:2000],
        "glossary": "\n".join(f"{a}: {b}" for a, b in terms.values() if b)[:8000],
        "documents": documents,
    }
    # Index and vocabulary remain inspectable. Recognition itself is audio-only.
    snapshot["context"] = ""
    return snapshot


def related(documents: list[dict], text: str, limit: int = 1200) -> str:
    query = set(re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower())) - STOP
    ranked = []
    for document in documents:
        for page in document["pages"]:
            tokens = set(re.findall(r"[a-z][a-z0-9_-]{2,}", page["text"].lower())) - STOP
            score = len(query & tokens)
            if score >= 2:
                ranked.append((score, f"{document['name']} p.{page['page']}: {page['text']}"))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return "\n".join(item[1] for item in ranked[:2])[:limit]
