"""Repair narrowly identifiable decimal and scale errors without rewriting surrounding speech."""

from __future__ import annotations

import re
from decimal import Decimal

_DIGITS = "zero one two three four five six seven eight nine".split()
_SMALL = (
    _DIGITS
    + "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
)
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = {"million": 10**6, "billion": 10**9, "trillion": 10**12}
_CARDINAL = "|".join([*_SMALL, *_TENS, "hundred", "thousand", "point", "oh"])
_DIGIT = "|".join([*_DIGITS, "oh"])
_DECIMAL = re.compile(
    rf"\bpoint\s+((?:{_DIGIT})(?:[ -]+(?:{_DIGIT}))*)\b|(?<![\w.])\d+\.\d+\b", re.I
)
_ZH_DECIMAL = re.compile(r"(?<![\d.])\d+\.\d+(?![\d.])")
_AMOUNT = re.compile(
    rf"(?<![\w.,])((?:\d[\d,]*(?:\.\d+)?|(?:{_CARDINAL})(?:[ -]+(?:{_CARDINAL}|and))*))"
    r"\s+(million|billion|trillion)\b",
    re.I,
)
_ZH_AMOUNT = re.compile(r"([\d,]+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)\s*(亿|万)")
_ZH_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"两": 2, "〇": 0}


def _under_hundred(parts):
    if len(parts) == 1 and parts[0] in _SMALL:
        return _SMALL.index(parts[0])
    if (
        parts
        and parts[0] in _TENS
        and (len(parts) == 1 or (len(parts) == 2 and parts[1] in _DIGITS[1:]))
    ):
        return 20 + 10 * _TENS.index(parts[0]) + (_DIGITS.index(parts[1]) if len(parts) == 2 else 0)
    return None


def _coefficient(text):
    if re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
        return Decimal(text.replace(",", ""))
    parts = re.split("[ -]+", text.lower())
    if len(parts) > 1 and parts[0] in _DIGITS[1:]:
        tail = parts[1:]
        if tail[0] == "hundred":
            tail = tail[1:]
            if not tail:
                return 100 * _DIGITS.index(parts[0])
            if tail[0] == "and":
                tail = tail[1:]
        elif tail[0] not in _TENS:
            return None
        remainder = _under_hundred(tail)
        return None if remainder is None else 100 * _DIGITS.index(parts[0]) + remainder
    return _under_hundred(parts)


def _chinese_coefficient(text):
    if text[0].isdecimal() or text[0] == ",":
        return _coefficient(text)
    # Adjacent nonzero digits or informal trailing hundreds ("一千五") are ambiguous.
    if re.search(r"[一二两三四五六七八九]{2}|[百千][一二两三四五六七八九]$", text):
        return None
    total = digit = 0
    for c in text:
        if c in _ZH_DIGITS:
            digit = _ZH_DIGITS[c]
        else:
            unit = {"十": 10, "百": 100, "千": 1000}[c]
            total += (digit or 1) * unit
            digit = 0
    return total + digit


# Changing a coefficient in a converted value can introduce a factor-of-ten error.
_CONVERTED_VALUE = re.compile(
    r"[百千萬万]分(?:比|率|之)|换算|折算|折合|科[学學][计計][数數]"
    r"|\d\s*(?:[%％‰‱成折]|[eE][+−-]?\d|[×✕xX*·]\s*10)"
    r"|10\s*(?:\^|[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺])"
)


def repair_numbers(source: str, chinese: str) -> str:
    """Use matching digit sequences as evidence; ambiguous or unpaired values pass through."""
    if _CONVERTED_VALUE.search(chinese):
        return chinese
    if re.search(r"\bpoint\b", source, re.I):
        decimals = list(_DECIMAL.finditer(source))
        rendered = list(_ZH_DECIMAL.finditer(chinese))
        if len(decimals) == len(rendered):
            for src, dst in reversed(list(zip(decimals, rendered, strict=True))):
                if not src[1] or not dst[0].startswith("0."):
                    continue
                # A nonzero whole part (or an identifier) is outside this repair.
                prior = re.search(r"([\w.]+)[ -]+$", source[: src.start()])
                if prior and re.fullmatch(rf"(?:{_CARDINAL}|\d+)", prior[1], re.I):
                    if prior[1].lower() not in {"0", "zero", "oh"}:
                        continue
                digits = "".join(
                    str(_DIGITS.index(w)) if w != "oh" else "0"
                    for w in re.split("[ -]+", src[1].lower())
                )
                tail = source[src.end() :]
                # Unit conversion can legitimately move a decimal point (e.g. to percent).
                if re.match(r"\s*(?:%|[A-Za-z])", tail) and not re.match(
                    r"\s+(?:and|or|is|was|for|but|to|of)\b", tail, re.I
                ):
                    continue
                if re.match(
                    r"\s*(?:[%‰％万亿]|美?分|厘|毫|微|千|米|角|倍|个?百分点)", chinese[dst.end() :]
                ):
                    continue
                if re.search(r"[百千万]分之\s*$", chinese[: dst.start()]):
                    continue
                actual = dst[0][2:]
                if (
                    sum(
                        m[0][2:].lstrip("0") == actual.lstrip("0")
                        for m in rendered
                        if m[0].startswith("0.")
                    )
                    != 1
                ):
                    continue
                if (
                    digits.startswith("0")
                    and len(digits) > len(actual)
                    and digits.lstrip("0") == actual.lstrip("0")
                    and digits.strip("0")
                ):
                    chinese = chinese[: dst.start()] + "0." + digits + chinese[dst.end() :]
    # Do not assign coefficients across multiple scales, ranges or compound Chinese units.
    if len(re.findall(r"\b(?:million|billion|trillion)\b", source, re.I)) != 1:
        return chinese
    amount = _AMOUNT.search(source)
    rendered = list(_ZH_AMOUNT.finditer(chinese))
    if not amount or len(rendered) != 1:
        return chinese
    dst = rendered[0]
    if (
        re.search(
            r"(?:[\d.\-/]|\b(?:half|quarter|point|hundred|thousand|or)\s+|\bto\s+)\s*$",
            source[: amount.start()],
            re.I,
        )
        or re.search(r"[点.万亿兆\d零一二两三四五六七八九十百千]$", chinese[: dst.start()])
        or re.match(r"[万亿兆零一二两三四五六七八九十百千\d]", chinese[dst.end() :])
    ):
        return chinese
    coefficient = _coefficient(amount[1])
    unit = 10**8 if dst[2] == "亿" else 10**4
    if (
        coefficient is None
        or not 0 < coefficient < 10**12
        or coefficient != _chinese_coefficient(dst[1])
    ):
        return chinese
    corrected = Decimal(coefficient) * _SCALES[amount[2].lower()] / unit
    return chinese[: dst.start(1)] + format(corrected, "f") + chinese[dst.end(1) :]
