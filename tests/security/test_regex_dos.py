"""Regex-Missbrauch: alle Muster mit Angriffseingaben in maximaler Zeilenlänge messen."""

from __future__ import annotations

import re
import time
from types import ModuleType

import pytest

from icware_auftragsimport.parsers import (
    address,
    common,
    contact,
    customer,
    dates,
    engine,
    numbers,
    order_lines,
    order_number,
    payment,
    shipping,
    text,
)
from icware_auftragsimport.security import redaction
from icware_auftragsimport.security.limits import PARSING
from support import analyze

MODULES: tuple[ModuleType, ...] = (
    address,
    common,
    contact,
    customer,
    dates,
    engine,
    numbers,
    order_lines,
    order_number,
    payment,
    shipping,
    text,
    redaction,
)
UNITS = (
    "a",
    "1",
    " ",
    "1 ",
    "a ",
    "-",
    ".",
    ",",
    "1.",
    "1,",
    "a1",
    "ä",
    "@",
    "x@",
    "+49 ",
    "0",
    "/",
    ":",
    "\t",
    "Str. ",
    "> ",
    "=",
    "*",
    "a-",
    "1-",
    "aa@b.",
    "Nr. ",
    "(",
)
SINGLE_SEARCH_BUDGET = 0.3


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    found: dict[int, tuple[str, re.Pattern[str]]] = {}

    def visit(name: str, value: object, depth: int = 0) -> None:
        if isinstance(value, re.Pattern) and isinstance(value.pattern, str):
            found[id(value)] = (name, value)
        elif isinstance(value, tuple | list) and depth < 3:
            for index, item in enumerate(value):
                visit(f"{name}[{index}]", item, depth + 1)

    for module in MODULES:
        for attribute, value in vars(module).items():
            visit(f"{module.__name__.rsplit('.', 1)[-1]}.{attribute}", value)
    return sorted(found.values(), key=lambda item: item[0])


PATTERNS = _patterns()


def test_inventory_is_complete() -> None:
    assert len(PATTERNS) >= 90


@pytest.mark.parametrize(("name", "pattern"), PATTERNS, ids=[n for n, _ in PATTERNS])
def test_no_catastrophic_backtracking(name: str, pattern: re.Pattern[str]) -> None:
    length = PARSING.max_line_chars
    worst = 0.0
    for unit in UNITS:
        attack = (unit * (length // len(unit) + 1))[:length]
        for candidate in (attack + "!", attack):
            start = time.perf_counter()
            pattern.search(candidate)
            worst = max(worst, time.perf_counter() - start)
    assert worst < SINGLE_SEARCH_BUDGET, f"{name}: {worst:.3f}s für eine Suche"


@pytest.mark.parametrize("unit", ["1 ", "a ", "Str. 1 ", "x@", "> ", "YT11 "])
def test_whole_pipeline_is_bounded(unit: str) -> None:
    start = time.perf_counter()
    analyze((unit * 300_000) + "\n" + ("Pos 1 " * 100 + "\n") * 2000)
    assert time.perf_counter() - start < 10
