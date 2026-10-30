"""Keep recognition grounded in audio; only already spoken terms may aid translation."""

import re

ASR_INPUT_POLICY = "audio_only_v1"


def matching_glossary(glossary: str, speech: str) -> str:
    """Never hand a translator a whole course vocabulary to complete or recite."""
    if not speech.strip():
        return ""
    matches = []
    for line in glossary.splitlines():
        english, separator, chinese = line.rpartition(": ")
        if not separator or not english.strip() or not chinese.strip():
            continue
        pattern = r"(?<![\w])" + re.escape(english.strip()) + r"(?![\w])"
        if re.search(pattern, speech, re.IGNORECASE):
            value = f"{english.strip()}: {chinese.strip()}"
            if len("\n".join(matches + [value])) <= 500:
                matches.append(value)
        if len(matches) == 8:
            break
    return "\n".join(matches)
