"""Check document links, archival integrity and requirement routing without network access."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from collections import Counter
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/archive/2026-11-05/manifest.json"
MARKER = "<!-- ARCHIVE BODY -->\n"
MD_LINK = re.compile(r"(?<=\]\()([^\s)]+)(?=\))")
HTML_LINK = re.compile(r'((?:href|src)=")([^"]+)(")')


def visible_markdown(text: str) -> str:
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            token = match.group(1)[0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def anchors(text: str) -> set[str]:
    result = set(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', text))
    seen: Counter[str] = Counter()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        heading = re.sub(r"<[^>]*>", "", unescape(heading)).lower()
        heading = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading)
        slug = "".join(
            c for c in heading
            if c in "_- " or unicodedata.category(c)[0] not in "PS"
        ).replace(" ", "-")
        n = seen[slug]
        seen[slug] += 1
        result.add(slug + (f"-{n}" if n else ""))
    return result


def main() -> None:
    errors = []
    manifest = json.loads(MANIFEST.read_text())
    sources = set()
    for item in manifest["documents"]:
        sources.add(item["source"])
        path = ROOT / item["archive"]
        if path.name == "AGENTS.md":
            errors.append(f"Historical agent rules must not auto-activate: {path}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["archive_sha256"]:
            errors.append(f"Archive file altered: {item['archive']}")
        body = data.decode().split(MARKER, 1)[1]
        reverse = {new: old for old, new in item["link_changes"]}
        body = MD_LINK.sub(lambda m, lookup=reverse: lookup.get(m.group(1), m.group(1)), body)
        body = HTML_LINK.sub(
            lambda m, lookup=reverse: m.group(1) + lookup.get(m.group(2), m.group(2)) + m.group(3),
            body,
        )
        if hashlib.sha256(body.encode()).hexdigest() != item["original_sha256"]:
            errors.append(f"Original body lost or modified: {item['source']}")
        if item["current"] and not (ROOT / item["current"]).is_file():
            errors.append(f"Missing current destination: {item['current']}")
    if len(sources) != 36 or len(manifest["documents"]) != 36:
        errors.append("Expected all 34 original docs plus README and AGENTS")
    # Full clones can also verify the inventory against the original commit.
    baseline = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", manifest["base_commit"]],
        cwd=ROOT, capture_output=True, text=True,
    )
    if baseline.returncode == 0:
        expected = {
            p for p in baseline.stdout.splitlines()
            if (p.startswith("docs/") and p.endswith(".md")) or p in {"README.md", "AGENTS.md"}
        }
        if expected != sources:
            errors.append(f"Baseline inventory mismatch: {sorted(expected ^ sources)}")

    files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").rglob("*.md"))]
    texts = {p.resolve(): visible_markdown(p.read_text()) for p in files}
    headings = {p: anchors(s) for p, s in texts.items()}
    checked = 0
    for path, text in texts.items():
        targets = [m.group(1) for m in MD_LINK.finditer(text)]
        targets += [m.group(2) for m in HTML_LINK.finditer(text)]
        for target in targets:
            parsed = urlsplit(unescape(target))
            if parsed.scheme or parsed.netloc:
                # Same-repository main links must not silently break after moving files.
                prefix = "https://github.com/lotus116/LectureFlow/blob/main/"
                if target.startswith(prefix):
                    parsed = urlsplit(target[len(prefix):])
                    destination = (ROOT / unquote(parsed.path)).resolve()
                else:
                    continue
            else:
                destination = (
                    (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
                )
            checked += 1
            label = str(path.relative_to(ROOT))
            if not destination.exists():
                errors.append(f"Broken target in {label}: {target}")
            elif parsed.fragment and destination.suffix == ".md":
                known = headings.get(destination)
                if known is None:
                    known = anchors(visible_markdown(destination.read_text()))
                if unquote(parsed.fragment) not in known:
                    errors.append(f"Broken heading in {label}: {target}")

    status = (ROOT / "docs/STATUS.md").read_text()
    for prefix, maximum in [("SD", 11), ("LF", 12), ("A", 10)]:
        for number in range(1, maximum + 1):
            identifier = f"{prefix}{'-' if prefix != 'A' else ''}{number:02d}"
            if identifier not in status:
                errors.append(f"Unrouted requirement: {identifier}")
    for identifier in ["A01a", "A01b", "A03a", "A03b", "A05a", "A05b"]:
        if identifier not in status:
            errors.append(f"Unrouted subtask: {identifier}")
    version = (ROOT / "VERSION").read_text().strip()
    for required in ["docs/USER_GUIDE.md", "docs/licenses/README.md", "docs/assets/logo.png",
                     f"docs/RELEASE-{version}.md"]:
        if not (ROOT / required).is_file():
            errors.append(f"Missing packaging/release input: {required}")
    for filename, pattern in [
        ("pyproject.toml", r'\[project\][\s\S]*?\nversion = "([^"]+)"'),
        ("uv.lock", r'name = "lectureflow"\nversion = "([^"]+)"'),
    ]:
        match = re.search(pattern, (ROOT / filename).read_text())
        if match is None or match.group(1) != version:
            errors.append(f"Project version mismatch: {filename}")
    mapping = (ROOT / "docs/archive/2026-11-05/MIGRATION.md").read_text()
    for source in sources:
        if f"`{source}`" not in mapping:
            errors.append(f"Missing migration row: {source}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {len(sources)} original documents preserved; {len(files)} Markdown files; "
          f"{checked} local/main links and anchors; SD/LF/A routing; "
          f"packaging inputs; version {version}.")


if __name__ == "__main__":
    main()
