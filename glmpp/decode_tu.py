#!/usr/bin/env python3
"""Decode Aliyun tu string table from dyn_pe077.js"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

SRC = Path("/tmp/dyn_pe077.js")
ARR_PATH = Path("/tmp/tu_array.json")

KEY_HEX = (
    "8dcd8bc0b19e8f93969cb7d3aca0b281ba928a99bc9aca89afa1b0cfadcec5cca982b9aaab94b4beb6a2c1c9979d889ba8aebb8ec8908cb595d7809fcb91bdb3bf"
)


def load_array() -> list[str]:
    if ARR_PATH.exists():
        return json.loads(ARR_PATH.read_text())
    src = SRC.read_text()
    i = src.find("function tu(")
    j = src.find("var e=", i)
    k = src.find(";return(tu=function", j)
    arr_src = src[j + 6 : k]
    arr = eval(arr_src, {"__builtins__": {}})
    ARR_PATH.write_text(json.dumps(arr))
    return arr


def make_key() -> list[int]:
    return [int(KEY_HEX[i : i + 2], 16) for i in range(0, len(KEY_HEX), 2)]


def tu_lw(s: str, n: int, a: list[int]) -> str:
    """Exact control flow of tu.lw.

    for (init; r=charAt(s++); decode_with_r)
        r = a.indexOf(248 ^ r.charCodeAt(0));
    """
    e = 0
    u = 0
    si = 0
    out: list[int] = []
    while si < len(s):
        r_ch = s[si]
        si += 1
        if not r_ch:
            break
        target = 248 ^ ord(r_ch)
        r = a.index(target) if target in a else -1
        # increment clause
        if r != -1:  # JS ~r truthy
            e = (64 * e + r) if (u % 4) else r
            ret = u % 4
            u += 1
            if ret:
                shift = (-2 * u) & 6
                byte = (255 & (e >> shift)) ^ n
                out.append(byte & 0xFF)
    pct = "".join("%%%02x" % b for b in out)
    return unquote(pct, encoding="utf-8", errors="replace")


def tu_lw_shift_old_u(s: str, n: int, a: list[int]) -> str:
    e = 0
    u = 0
    si = 0
    out: list[int] = []
    while si < len(s):
        r_ch = s[si]
        si += 1
        if not r_ch:
            break
        target = 248 ^ ord(r_ch)
        r = a.index(target) if target in a else -1
        if r != -1:
            e = (64 * e + r) if (u % 4) else r
            ret = u % 4
            old_u = u
            u += 1
            if ret:
                shift = (-2 * old_u) & 6
                byte = (255 & (e >> shift)) ^ n
                out.append(byte & 0xFF)
    pct = "".join("%%%02x" % b for b in out)
    return unquote(pct, encoding="utf-8", errors="replace")


def ta_index(t: int) -> int:
    """ta(t,n) uses tu(t-2, n); tu indexes array with n-=3 => arr[t-5]."""
    return t - 5


def main() -> None:
    arr = load_array()
    a = make_key()
    print(f"arr={len(arr)} key={len(a)}")

    # map coverage
    std = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    mapped = {}
    unmapped = []
    for ch in std:
        v = 248 ^ ord(ch)
        if v in a:
            mapped[ch] = a.index(v)
        else:
            unmapped.append(ch)
    print(f"mapped={len(mapped)} unmapped={unmapped}")
    print(f"mapped={mapped}")

    # a(28,77)=ta(70,28) => arr[65]
    # a(98,274)=ta(267,98) => arr[262]
    tests = [
        ("a28_77", 65, 28),
        ("a98_274", 262, 98),
    ]
    for name, idx, n in tests:
        if not (0 <= idx < len(arr)):
            print(name, "OOB", idx)
            continue
        for label, fn in [("new_u", tu_lw), ("old_u", tu_lw_shift_old_u)]:
            try:
                r = fn(arr[idx], n, a)
                print(f"{name} {label}: {r[:200]!r}")
            except Exception as exc:
                print(f"{name} {label}: ERR {exc}")

    # brute a few array slots with n in common set
    print("\n-- brute readable --")
    readable = []
    for i, s in enumerate(arr):
        for n in (0, 28, 98, 77, 274, 91):
            for fn in (tu_lw, tu_lw_shift_old_u):
                try:
                    r = fn(s, n, a)
                except Exception:
                    continue
                if r and all(32 <= ord(c) < 127 or c in "\n\t" for c in r) and len(r) >= 4:
                    if any(c.isalpha() for c in r):
                        readable.append((i, n, fn.__name__, r[:80]))
    # unique-ish
    seen = set()
    for item in readable:
        key = (item[3], item[2])
        if key in seen:
            continue
        seen.add(key)
        print(item)
        if len(seen) > 40:
            break


if __name__ == "__main__":
    main()
