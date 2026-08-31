r"""RFC 8785 JSON Canonicalization Scheme (JCS), stdlib only.

WHY THIS EXISTS
---------------
`draft-sharif-agent-audit-trail` specifies SHA-256 hash chaining over **RFC 8785**.
Until 0.4.0 this project hashed `json.dumps(sort_keys=True, separators=(",", ":"))`,
which is the same *idea* and a different *digest*: JCS mandates ES6 number formatting
and a specific escaping and key-ordering rule. Two implementations that disagree on any
of those produce different chains from identical records, which is exactly the failure a
canonicalization spec exists to prevent.

`record_hash`'s docstring has always promised "a verifier on another machine, in another
language, must be able to recompute this". That promise is only true against a published
spec, so this replaces a hand-rolled near-miss with the real one.

Stdlib only, deliberately: this logic is copied into every EvidencePack verifier, and a
pack that needed a pip install to check would defeat the point of the pack.

THE THREE THINGS JCS ACTUALLY REQUIRES (and that a naive dumps gets wrong)
-------------------------------------------------------------------------
1. Object keys sorted by **UTF-16 code unit**, not code point. These agree across the
   BMP and disagree for astral characters, where a surrogate pair sorts below U+E000.
2. **ES6 number serialization.** `1.0` is `1`, `1e21` is `1e+21`, and the shortest
   round-tripping form wins. Python's `repr` is shortest-round-trip too but formats
   integral floats and exponents differently.
3. Minimal string escaping: only `"` `\` and C0 controls, with the short forms where
   they exist.
"""
from __future__ import annotations

import decimal
import math

__all__ = ["canonicalize", "serialize"]

# RFC 8785 section 3.2.2.2: the short escapes, everything else below 0x20 as \u00xx.
_SHORT_ESCAPES = {
    0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r",
    0x22: '\\"', 0x5C: "\\\\",
}


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        esc = _SHORT_ESCAPES.get(cp)
        if esc is not None:
            out.append(esc)
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _number(value: float | int) -> str:
    """ES6 Number::toString, which is what RFC 8785 defers to for numbers.

    Python and JavaScript agree on the DIGITS (both emit the shortest string that
    round-trips) and disagree on the FORM. ES6 picks decimal vs exponential from the
    decimal-point position n, using decimal when -6 < n <= 21. Python's repr switches to
    exponential from 1e-5 down, so the two disagree across [1e-6, 1e-4): ES6 wants
    0.000001 where repr gives 1e-06.

    Caught by probing past the RFC's supplied vectors: all four reference vectors passed
    while this was still wrong, and a float in that window inside a `meta` field would
    have produced a chain no conforming verifier could reproduce.
    """
    if isinstance(value, bool):                      # bool is an int subclass in Python
        raise TypeError("bool is not a JSON number")
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError("NaN and Infinity are not valid JSON (RFC 8785 forbids them)")
    if value == 0:
        return "0"                                   # JCS normalises -0 to 0

    d = decimal.Decimal(repr(value))                 # repr = shortest round-trip
    sign, digit_tuple, exponent = d.as_tuple()
    digits = "".join(str(x) for x in digit_tuple).rstrip("0") or "0"
    # n is where the decimal point sits relative to the significant digits.
    n = len(digit_tuple) + exponent
    neg = "-" if sign else ""

    if -6 < n <= 21:                                 # ES6 decimal range
        if n >= len(digits):
            body = digits + "0" * (n - len(digits))
        elif n > 0:
            body = digits[:n] + "." + digits[n:]
        else:
            body = "0." + "0" * (-n) + digits
        return neg + body

    e = n - 1                                        # ES6 exponential form
    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{neg}{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"


def _key_order(key: str) -> tuple[int, ...]:
    """Sort key: the string's UTF-16 code units.

    RFC 8785 section 3.2.3 orders by UTF-16 code unit, so an astral character (encoded
    as a surrogate pair beginning 0xD800) sorts BELOW U+E000, while Python's default
    string comparison, being by code point, would sort it above.
    """
    return tuple(key.encode("utf-16-be")[i] << 8 | key.encode("utf-16-be")[i + 1]
                 for i in range(0, len(key.encode("utf-16-be")), 2))


def serialize(value) -> str:
    """Canonical JSON text for a JSON-compatible Python value."""
    if value is None:
        return "null"
    if isinstance(value, bool):                      # BEFORE int: bool subclasses int
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(serialize(v) for v in value) + "]"
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise TypeError(f"JCS object keys must be strings, got {type(k).__name__}")
        items = sorted(value.items(), key=lambda kv: _key_order(kv[0]))
        return "{" + ",".join(f"{_string(k)}:{serialize(v)}" for k, v in items) + "}"
    raise TypeError(f"not JSON-serializable under JCS: {type(value).__name__}")


def canonicalize(value) -> bytes:
    """Canonical UTF-8 bytes, which is what gets hashed."""
    return serialize(value).encode("utf-8")
