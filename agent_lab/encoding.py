"""Encoding: the small set of rules that stop silent data corruption.

Every function here exists because of a real failure mode:

* text decoded with the wrong codec (mojibake)
* a UTF-8 BOM left on the front of a string
* CRLF vs LF changing a hash
* NFC vs NFD making two "identical" names differ byte-for-byte
* hashing fields by concatenation without a separator, so ("ab","c") and
  ("a","bc") collide
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field

UTF8 = "utf-8"

# Two different corruptions, often confused because they look different on
# screen but are the same mistake (UTF-8 bytes read with an 8-bit codec):
#
#   bytes.decode("latin-1")  -> C1 control characters (invisible / box glyphs)
#   bytes.decode("cp1252")   -> the printable "â€™" everyone recognises
#
# Windows tools default to cp1252 for "ANSI" files, which is why the printable
# form is the one that shows up in reports.

# Markers produced when UTF-8 bytes are decoded as Windows-1252 (printable).
MOJIBAKE_MARKERS: dict[str, str] = {
    "\u00e2\u20ac\u2122": "right single quote",
    "\u00e2\u20ac\u0153": "left double quote",
    "\u00e2\u20ac\u009d": "right double quote",
    "\u00e2\u20ac\u201c": "en dash",
    "\u00e2\u20ac\u201d": "em dash",
    "\u00c3\u00a9": "e acute",
    "\u00c2\u00a3": "pound sign",
    "\u00c3\u00a2": "a-circumflex sequence",
    "\u00f0\u0178": "emoji prefix",
    "\ufffd": "already-lost character (replacement char)",
}

# Markers produced when the same bytes are decoded as Latin-1: the 0x80-0x9F
# range becomes C1 control characters instead of printable symbols.
LATIN1_C1_MARKERS: dict[str, str] = {
    "\u00e2\u0080": "multi-byte lead (C1 control range)",
    "\u00c3": "two-byte lead",
}


def detect_mojibake(text: str) -> dict[str, int]:
    """Report every mojibake marker present, printable or control-character."""
    found = {m: text.count(m) for m in MOJIBAKE_MARKERS if m in text}
    for m in LATIN1_C1_MARKERS:
        count = text.count(m)
        if count:
            found[m] = found.get(m, 0) + count
    return found


@dataclass
class Diagnosis:
    text: str
    had_bom: bool
    newline_style: str
    mojibake: dict[str, int] = field(default_factory=dict)
    normalized: bool = False

    @property
    def clean(self) -> bool:
        return not self.had_bom and not self.mojibake


def read_text_strict(path: str) -> tuple[str, Diagnosis]:
    """Read a file as UTF-8 and report what was wrong with it.

    We deliberately do NOT pass errors="replace": that turns corruption into
    an ordinary-looking string, which is how bad data reaches production.
    """
    raw = open(path, "rb").read()  # noqa: SIM115 - explicit binary read is the point
    return decode_strict(raw)


def decode_strict(raw: bytes) -> tuple[str, Diagnosis]:
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    style = "CRLF" if b"\r\n" in raw else "LF"
    text = raw.decode(UTF8)  # raises UnicodeDecodeError rather than corrupting
    return text, Diagnosis(
        text=text,
        had_bom=had_bom,
        newline_style=style,
        mojibake=detect_mojibake(text),
    )


def repair_mojibake(text: str) -> str:
    """Undo one round of UTF-8-read-with-an-8-bit-codec corruption.

    Tries cp1252 first (the printable form), then latin-1 (the C1 form).
    """
    for codec in ("cp1252", "latin-1"):
        try:
            candidate = text.encode(codec).decode(UTF8)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate != text:
            return candidate
    return text


def normalise(text: str, form: str = "NFC") -> str:
    """Canonical Unicode form. Without this, two names that look identical
    compare unequal and hash differently."""
    return unicodedata.normalize(form, text)


def to_lf(text: str) -> str:
    """Git, Windows tools and hand edits disagree about line endings."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def bind_digest(*fields: str) -> str:
    """SHA-256 over fields joined by NUL.

    NUL cannot appear in UTF-8 text fields, so ("ab","c") and ("a","bc")
    produce different digests. Plain concatenation would collide.
    """
    material = "\0".join(fields).encode(UTF8)
    return hashlib.sha256(material).hexdigest()


def naive_digest(*fields: str) -> str:
    """The version that looks fine and is wrong. Used by lesson 02 to show why."""
    return hashlib.sha256("".join(fields).encode(UTF8)).hexdigest()


def canonical_digest(contact_id: int, subject: str, body: str) -> str:
    """Exactly the binding the Stillroom acquisition app uses:
    UTF-8 `contact_id + NUL + subject + NUL + body`.
    """
    material = f"{contact_id}\0{subject}\0{body}".encode(UTF8)
    return hashlib.sha256(material).hexdigest()
