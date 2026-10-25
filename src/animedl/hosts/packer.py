"""Unpacker for Dean Edwards' P.A.C.K.E.R. (`eval(function(p,a,c,k,e,d){...}`).

Most video hosts (streamwish, vidhide, filemoon, mp4upload, mixdrop...) hide their player
configuration this way.
"""

from __future__ import annotations

import re
import string

_PACKED = re.compile(
    r"eval\(function\(p,a,c,k,e,[rd]\).*?\}\s*\(\s*"
    r"'(?P<payload>(?:[^'\\]|\\.)*)'\s*,\s*(?P<radix>\d+|\[\])\s*,\s*(?P<count>\d+)\s*,\s*"
    r"'(?P<symtab>(?:[^'\\]|\\.)*)'\.split\('\|'\)",
    re.S,
)

_ALPHABET_62 = string.digits + string.ascii_lowercase + string.ascii_uppercase
_ALPHABET_95 = "".join(chr(c) for c in range(32, 127))


def _unbase(word: str, radix: int) -> int:
    if radix <= 36:
        return int(word, radix)
    alphabet = _ALPHABET_62 if radix <= 62 else _ALPHABET_95
    value = 0
    for char in word:
        value = value * radix + alphabet.index(char)
    return value


def unpack(payload: str, radix: int, count: int, symtab: list[str]) -> str:
    payload = re.sub(r"\\(.)", r"\1", payload)  # undo JS string escapes (\' and \\)

    def lookup(match: re.Match) -> str:
        word = match.group(0)
        try:
            index = _unbase(word, radix)
        except ValueError:
            return word
        if index < len(symtab) and symtab[index]:
            return symtab[index]
        return word

    return re.sub(r"\b\w+\b", lookup, payload)


def unpack_all(html: str) -> list[str]:
    """Unpack every packed script found in the page."""
    out = []
    for m in _PACKED.finditer(html):
        radix = 62 if m["radix"] == "[]" else int(m["radix"])
        symtab = m["symtab"].replace("\\'", "'").split("|")
        try:
            out.append(unpack(m["payload"], radix, int(m["count"]), symtab))
        except Exception:  # malformed block, keep going with the rest
            continue
    return out


def is_packed(html: str) -> bool:
    return bool(_PACKED.search(html))
