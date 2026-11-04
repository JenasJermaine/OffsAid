"""Plumbing check against the real scam-language model.

The messages below were written by the same people who wrote the rules, and they
were already run in Colab. A pass means the saved model loads and the labels they
saw still come back. It is not an accuracy test. Real-world accuracy has not been
measured.

Extra labels on a message that expects some are tolerated. Message 2 may also
raise visa_travel_arranged; that is a known false positive on delivery wording
and is not patched here. Messages that expect nothing must come back empty.
"guaranteed_outcome only" must not pick up a second label.

Run from the repo root, with the model unpacked:

    .venv\\Scripts\\python.exe scripts/smoke_scam_language.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.scam_language import ScamLanguageChecker  # noqa: E402

MODEL_DIR = ROOT / "models" / "offsaid_classifier_v4"

# (text, expected labels, allow extra labels)
CASES = [
    (
        "Congratulations bro! The club has picked you. Only thing left is paying the courier for your papers.",
        {"guaranteed_outcome", "advance_fee_request"},
        True,
    ),
    (
        "Hi, DHL is holding your contract until the delivery fee is paid. Send it today.",
        {"urgency_pressure", "advance_fee_request"},
        True,
    ),
    (
        "Hakuna mtu atakuhakikishia visa, ni ubalozi unaoamua. Trial ni bure.",
        set(),
        False,
    ),
    (
        "We cannot promise any visa, the embassy decides. Come to the open trial, no charge.",
        set(),
        False,
    ),
    (
        "The doctor must be paid before your medical, send the money to my sister's number 0722 111 222.",
        {"personal_account_payment", "advance_fee_request"},
        True,
    ),
    (
        "Pole kwa kuchelewa. Tuma ile pesa ya customs ndio documents zitoke, usiwaambie wengine.",
        {"secrecy", "advance_fee_request"},
        True,
    ),
    (
        "Congratulations on your trial. The coach will go through all players and we will email results next week.",
        set(),
        False,
    ),
    (
        "Your flight is booked and your visa is approved, just settle the clearance charges.",
        {"visa_travel_arranged", "advance_fee_request"},
        True,
    ),
    (
        "We can't guarantee the visa, but your contract is 100% guaranteed.",
        {"guaranteed_outcome"},
        False,
    ),
    (
        "Your trial is on Saturday at 9am. Bring boots and ID. There is no fee.",
        set(),
        False,
    ),
    (
        "The club will pay for your courier and documents, you pay nothing.",
        set(),
        False,
    ),
    (
        "Please send your medical results and passport copy by email.",
        set(),
        False,
    ),
]


def _working_set_mb() -> float | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    if not ok:
        return None
    return counters.WorkingSetSize / (1024 * 1024)


def main() -> int:
    if not MODEL_DIR.is_dir():
        print(f"Model folder not found: {MODEL_DIR}")
        return 1
    started = time.perf_counter()
    checker = ScamLanguageChecker.load(MODEL_DIR)
    load_s = time.perf_counter() - started
    print(f"loaded in {load_s:.1f}s  mode={checker.mode}  working_set_mb={_working_set_mb()}")

    failures = []
    for index, (text, expected, allow_extra) in enumerate(CASES, start=1):
        flags = checker.classify([{"sender": "agent", "text": text}])["flags"]
        got = {flag["label"] for flag in flags}
        missing = expected - got
        extra = got - expected
        sources = {flag["label"]: flag["source"] for flag in flags}
        problem = ""
        if missing or (extra and not allow_extra):
            problem = "FAIL"
            failures.append(index)
        note = ""
        if index == 2 and "visa_travel_arranged" in got:
            note = "  known false positive: visa_travel_arranged on delivery wording"
        elif index == 2:
            note = "  known false positive did not appear this run"
        print(f"{index:2} {problem or 'ok':4} {sorted(got)} sources={sources}{note}")
        if problem:
            print(f"    expected {sorted(expected)} missing={sorted(missing)} extra={sorted(extra)}")

    five = [{"sender": "agent", "text": text} for text, _, _ in CASES[:5]]
    t0 = time.perf_counter()
    checker.classify(five)
    elapsed = time.perf_counter() - t0
    print(f"five-message chat: {elapsed:.2f}s  working_set_mb={_working_set_mb()}")
    if failures:
        print(f"{len(failures)} message(s) did not match: {failures}")
        return 1
    print("smoke check passed (plumbing only, not an accuracy measurement)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
