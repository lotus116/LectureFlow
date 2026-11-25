"""Read aligned, organizer-provided ASR and references from the official ACL 60/60 ZIP."""

import html
import re
from dataclasses import dataclass
from zipfile import ZipFile

from score import tokens


@dataclass
class Talk:
    split: str
    paper: str
    abstract: str
    ids: list
    asr: list
    references: list


def talks(archive: ZipFile):
    def lines(name):
        return archive.read(name).decode("utf-8-sig").splitlines()

    for split in ("dev", "eval"):
        raw = lines(f"2/intermediate_files/transcription/asr_output/{split}.asr-reseg.en.txt")
        corrected = "2/intermediate_files/transcription/postedit_domain"
        refs = lines(f"{corrected}/{split}.asr-postedit-domain.en.txt")
        source = lines(f"2/acl_6060/{split}/text/xml/ACL.6060.{split}.en-xx.en.xml")
        content = "\n".join(source)
        start = 0
        # The distributed XML contains bare ampersands. Read its single-line segments literally.
        for paper, body in re.findall(r'<doc docid="([^"]+)"[^>]*>(.*?)</doc>', content, re.S):
            segment_text = [
                html.unescape(s) for s in re.findall(r'<seg id="\d+">(.*?)</seg>', body)
            ]
            end = start + len(segment_text)
            if not segment_text or list(map(tokens, segment_text)) != list(
                map(tokens, refs[start:end])
            ):
                raise ValueError(f"Reference files disagree in {split}/{paper}")
            abstract = html.unescape(re.search(r"<abstract>(.*?)</abstract>", body, re.S)[1])
            yield Talk(
                split,
                paper,
                abstract,
                list(range(start + 1, end + 1)),
                raw[start:end],
                refs[start:end],
            )
            start = end
        if start != len(raw) or start != len(refs):
            raise ValueError(f"Segment count mismatch in {split}")
