"""RFC 8785 (JCS) conformance.

The Agent Audit Trail draft is normative about this: "Implementations MUST use JCS
(RFC 8785) for canonicalization. Alternative canonicalization schemes MUST NOT be used,
as they would break chain verification across implementations." So a bug here is not a
formatting nit, it is a chain that no conforming verifier can reproduce.

The number tests exist because the four reference vectors ALL PASSED while the
implementation was still wrong: Python's repr switches to exponential from 1e-5 down and
ES6 not until 1e-7, so everything in [1e-6, 1e-4) serialized incorrectly and no supplied
vector happened to cover it. Test the spec's RULE, not only the spec's examples.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from homestead_memory.core import jcs

FIXTURES = Path(__file__).parent / "fixtures" / "jcs"


@pytest.mark.parametrize("name", ["weird", "french", "structures", "unicode"])
def test_reference_vectors(name):
    """Byte-for-byte against the reference implementation's own testdata."""
    value = json.loads((FIXTURES / f"{name}.input.json").read_text(encoding="utf-8"))
    expected = (FIXTURES / f"{name}.expected.json").read_bytes()
    assert jcs.canonicalize(value) == expected


# Left column is JavaScript String(x), which is what RFC 8785 defers to.
@pytest.mark.parametrize("expected,value", [
    ("1", 1.0),                       # ES6 drops a trailing .0
    ("0", -0.0),                      # negative zero normalises
    ("-1.5", -1.5),
    ("0.0001", 1e-4),
    ("0.00001", 1e-5),                # regression: repr says 1e-05
    ("0.000001", 1e-6),               # regression: repr says 1e-06
    ("0.0000025", 2.5e-6),            # regression
    ("-0.000001", -1e-6),             # regression, signed
    ("1e-7", 1e-7),                   # boundary: exponential starts here
    ("1.234e-7", 1.234e-7),
    ("100000000000000000000", 1e20),  # boundary: decimal up to n <= 21
    ("1e+21", 1e21),                  # boundary: exponential from here
    ("5e-324", 5e-324),               # min subnormal
    ("1.7976931348623157e+308", 1.7976931348623157e308),
    ("9007199254740992", 9007199254740992),
])
def test_es6_number_forms(expected, value):
    assert jcs.serialize(value) == expected


def test_keys_sort_by_utf16_code_unit_not_code_point():
    """RFC 8785 3.2.3 orders by UTF-16 code unit, not code point.

    An astral character encodes as a surrogate pair starting 0xD800, so in UTF-16 it
    sorts BELOW U+E000, while Python's default string comparison, being by code point,
    puts it above. Sorting naively yields a different and non-conforming digest.
    """
    astral = "\U0001F600"        # U+1F600, encodes as D83D DE00
    bmp = "\uE000"               # U+E000, a single code unit above D83D

    assert astral > bmp, "code-point order puts the astral char second"
    out = jcs.serialize({bmp: 1, astral: 2})
    assert out.index(astral) < out.index(bmp), (
        f"UTF-16 order must put the surrogate pair FIRST, got {out!r}"
    )


def test_rejects_values_json_cannot_represent():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            jcs.serialize(bad)
    with pytest.raises(TypeError):
        jcs.serialize({1: "int key is not valid JCS"})
    with pytest.raises(TypeError):
        jcs.serialize({"s"})                       # a set is not JSON


def test_booleans_are_not_numbers():
    """bool subclasses int in Python; serialising True as 1 would corrupt a record."""
    assert jcs.serialize({"a": True, "b": 1}) == '{"a":true,"b":1}'


def test_output_is_utf8_bytes_and_has_no_whitespace():
    out = jcs.canonicalize({"b": [1, {"d": "x"}], "a": "y"})
    assert isinstance(out, bytes)
    assert out == b'{"a":"y","b":[1,{"d":"x"}]}'
