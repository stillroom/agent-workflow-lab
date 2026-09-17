"""Lesson 01 — encoding.

Every test in this file is a *demonstration of a real failure mode*, not a
style preference. Run it and read the assertions; they are the lesson.

    .venv/bin/python -m pytest lessons/lesson_01_encoding/test_encoding.py -v
"""

from __future__ import annotations

import unicodedata

import pytest

from agent_lab.encoding import (
    bind_digest,
    canonical_digest,
    decode_strict,
    detect_mojibake,
    naive_digest,
    normalise,
    repair_mojibake,
    to_lf,
)

# --------------------------------------------------------------------------
# 1. Wrong codec = mojibake. UTF-8 bytes read as Latin-1, then re-encoded.
# --------------------------------------------------------------------------


def test_utf8_read_as_cp1252_produces_the_printable_mojibake() -> None:
    """This is how curly quotes become 'â€™' in a report.

    Windows "ANSI" files are cp1252, so this is the form people actually see.
    """
    original = "Adam’s review request — “done"
    corrupted = original.encode("utf-8").decode("cp1252")

    assert corrupted != original
    assert "â€™" in corrupted  # the tell-tale marker
    assert "â€”" in corrupted  # em dash
    assert repair_mojibake(corrupted) == original


def test_cp1252_undefined_bytes_cause_unrecoverable_loss() -> None:
    """cp1252 leaves 0x81 0x8D 0x8F 0x90 0x9D undefined.

    U+201D (right double quote) is E2 80 **9D** in UTF-8, so decoding those
    bytes as cp1252 with a lossy error handler destroys them. The result is
    NOT repairable by re-encoding, because the byte is already gone.
    """
    original = "“done”"
    try:
        original.encode("utf-8").decode("cp1252")
        raise AssertionError("expected cp1252 to reject the undefined 0x9D byte")
    except UnicodeDecodeError as exc:
        assert "0x9d" in str(exc).lower()

    lossy = original.encode("utf-8").decode("cp1252", errors="replace")
    assert "\ufffd" in lossy                  # replacement char present
    assert repair_mojibake(lossy) != original  # cannot be undone
    assert detect_mojibake(lossy)["\ufffd"] >= 1


def test_utf8_read_as_latin1_gives_invisible_control_characters() -> None:
    """The same bug with latin-1 instead of cp1252.

    Bytes 0x80-0x9F have no printable Latin-1 equivalent, so they land as C1
    control characters. It looks like boxes or nothing at all — which is why
    people think the file is 'fine' until something compares bytes.
    """
    original = "Adam’s notes"
    corrupted = original.encode("utf-8").decode("latin-1")

    assert corrupted != original
    assert "\x80\x99" in corrupted          # raw C1 bytes, invisible on screen
    assert "â€™" not in corrupted           # NOT the printable form
    assert repair_mojibake(corrupted) == original
    assert detect_mojibake(corrupted), "control-character form must still be detected"


def test_repair_is_not_magic() -> None:
    """Repair only works because Latin-1 maps every byte to one character.
    Read the SAME bytes with a lossy codec instead and the data is gone."""
    original = "Adam’s notes"
    lossy = original.encode("utf-8").decode("utf-8", errors="replace")
    assert lossy == original  # no corruption when the codec is correct

    # Correct codec in, wrong codec out -> unrecoverable question marks.
    destroyed = original.encode("ascii", errors="replace").decode("ascii")
    assert destroyed == "Adam?s notes"
    assert repair_mojibake(destroyed) != original  # cannot be undone


# --------------------------------------------------------------------------
# 2. Strict decode raises instead of silently corrupting.
# --------------------------------------------------------------------------


def test_strict_decode_raises_on_invalid_utf8() -> None:
    bad = b"caf\xe9"  # Latin-1 encoded e-acute, not valid UTF-8
    with pytest.raises(UnicodeDecodeError):
        decode_strict(bad)


def test_strict_decode_strips_bom_and_reports_newlines() -> None:
    text, diag = decode_strict(b"\xef\xbb\xbfHello\r\nWorld\r\n")
    assert text == "Hello\r\nWorld\r\n"
    assert diag.had_bom is True
    assert diag.newline_style == "CRLF"
    assert diag.clean is False  # BOM present


def test_diagnosis_flags_mojibake_markers() -> None:
    """A file that was already corrupted and saved as UTF-8 is detectable."""
    corrupted = "report — done".encode("utf-8").decode("cp1252")
    _text, diag = decode_strict(corrupted.encode("utf-8"))
    assert diag.mojibake, "expected mojibake markers to be reported"
    assert diag.clean is False


# --------------------------------------------------------------------------
# 3. Line endings change hashes.
# --------------------------------------------------------------------------


def test_line_endings_change_the_digest() -> None:
    lf = "line one\nline two\n"
    crlf = "line one\r\nline two\r\n"
    assert bind_digest("body", lf) != bind_digest("body", crlf)
    assert to_lf(crlf) == lf
    assert bind_digest("body", to_lf(crlf)) == bind_digest("body", lf)


# --------------------------------------------------------------------------
# 4. Unicode normalisation: identical-looking names, different bytes.
# --------------------------------------------------------------------------


def test_nfc_and_nfd_look_the_same_and_are_not() -> None:
    composed = "Café"                      # e-acute as one code point
    decomposed = unicodedata.normalize("NFD", composed)  # e + combining acute

    assert composed != decomposed
    assert len(composed) != len(decomposed)
    assert normalise(decomposed) == normalise(composed)
    assert bind_digest("name", composed) != bind_digest("name", decomposed)
    assert bind_digest("name", normalise(composed)) == bind_digest(
        "name", normalise(decomposed)
    )


# --------------------------------------------------------------------------
# 5. Field binding: why we join with NUL.
# --------------------------------------------------------------------------


def test_concatenation_collides_and_nul_joining_does_not() -> None:
    """('ab','c') and ('a','bc') concatenate to the same string.

    For a draft bound to a contact ID and subject, that is a real
    approval-bypass shape: two different artifacts share one digest.
    """
    assert naive_digest("ab", "c") == naive_digest("a", "bc")   # collision
    assert bind_digest("ab", "c") != bind_digest("a", "bc")     # no collision


def test_canonical_digest_matches_the_acquisition_app_rule() -> None:
    """The Stillroom acquisition app binds
    UTF-8 `contact_id + NUL + subject + NUL + body`.
    """
    import hashlib

    contact_id, subject, body = 1, "invoice follow-up", "Hi,\nThanks.\n"
    expected = hashlib.sha256(f"{contact_id}\0{subject}\0{body}".encode()).hexdigest()

    assert canonical_digest(contact_id, subject, body) == expected
    assert len(canonical_digest(contact_id, subject, body)) == 64


def test_digest_is_stable_across_equivalent_but_unnormalised_unicode() -> None:
    """Normalise BEFORE hashing, or the same name yields two digests."""
    a = "José"
    b = unicodedata.normalize("NFD", a)
    assert canonical_digest(1, "s", a) != canonical_digest(1, "s", b)
    assert canonical_digest(1, "s", normalise(a)) == canonical_digest(1, "s", normalise(b))
