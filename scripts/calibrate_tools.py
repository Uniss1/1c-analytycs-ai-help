#!/usr/bin/env python3
"""Calibration script for Gemma 4 E2B tool calling.

Tests tool selection and parameter filling against a set of reference questions.
Run: python3 scripts/calibrate_tools.py [--model gemma4:e2b] [--url http://localhost:11434]

Prints a table: question → expected tool → actual tool → params → pass/fail.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.tool_caller import call_with_tools
from api.tool_defs import build_tools
from api.metadata import init_metadata, get_all_registers

DB_PATH = str(Path(__file__).parent.parent / "metadata.db")


def load_test_register() -> dict:
    """Load first register from metadata.db (no hardcoded metadata)."""
    init_metadata(DB_PATH)
    registers = get_all_registers()
    if not registers:
        print("ОШИБКА: metadata.db пуст. Запустите: python3 scripts/seed_metadata.py")
        sys.exit(1)
    reg = registers[0]
    print(f"Регистр: {reg['name']} ({len(reg['dimensions'])} измерений, {len(reg['resources'])} ресурсов)")
    return reg

# --- Test cases: (question, expected_tool, expected_params_subset) ---

TEST_CASES = [
    # Aggregate
    # Aggregate
    (
        "Какая выручка за март 2025?",
        "aggregate",
        {"resource": "Сумма", "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Сколько EBITDA по факту за январь 2025?",
        "aggregate",
        {"resource": "Сумма", "metric": "EBITDA", "scenario": "Факт", "year": 2025, "month": 1},
    ),
    (
        "Прогноз выручки на декабрь 2025",
        "aggregate",
        {"resource": "Сумма", "scenario": "Прогноз", "metric": "Выручка", "year": 2025, "month": 12},
    ),
    (
        "План по ОЗП на февраль 2025 для ДЗО-1",
        "aggregate",
        {"resource": "Сумма", "scenario": "План", "metric": "ОЗП", "company": "ДЗО-1", "year": 2025, "month": 2},
    ),

    # Group by
    (
        "Выручка по ДЗО за март 2025",
        "group_by",
        {"group_by": "company", "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Маржа в разрезе показателей за январь 2025",
        "group_by",
        {"group_by": "metric", "year": 2025, "month": 1},
    ),
    (
        "Факт по сценариям за март 2025",
        "group_by",
        {"group_by": "scenario", "year": 2025, "month": 3},
    ),

    # Top N
    (
        "Топ-3 ДЗО по выручке за март 2025",
        "top_n",
        {"group_by": "company", "limit": 3, "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Топ-5 показателей за январь 2025",
        "top_n",
        {"group_by": "metric", "limit": 5, "year": 2025, "month": 1},
    ),

    # Time series
    (
        "Динамика выручки по месяцам за 2025 год",
        "time_series",
        {"metric": "Выручка"},
    ),
    (
        "Тренд EBITDA помесячно",
        "time_series",
        {"metric": "EBITDA"},
    ),

    # --- New tools ---

    # Compare
    (
        "Сравни факт и план по выручке за март 2025",
        "compare",
        {"resource": "Сумма", "compare_by": "scenario", "values": ["Факт", "План"],
         "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Факт vs бюджет EBITDA за январь 2025",
        "compare",
        {"compare_by": "scenario", "metric": "EBITDA", "year": 2025, "month": 1},
    ),

    # Ratio
    (
        "Рентабельность за март 2025",
        "ratio",
        {"numerator": "Маржа", "denominator": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Маржа к выручке за январь 2025",
        "ratio",
        {"numerator": "Маржа", "denominator": "Выручка", "year": 2025, "month": 1},
    ),

    # Filtered
    (
        "ДЗО где выручка больше 100 млн за март 2025",
        "filtered",
        {"group_by": "company", "condition_operator": ">", "metric": "Выручка",
         "year": 2025, "month": 3},
    ),
    (
        "Показатели с суммой меньше 10 млн за январь 2025",
        "filtered",
        {"group_by": "metric", "condition_operator": "<", "year": 2025, "month": 1},
    ),

    # --- Negative / edge cases ---
    (
        "Какая выручка?",
        "aggregate",
        {"resource": "Сумма", "metric": "Выручка"},
    ),
]


def check_params(expected: dict, actual_args: dict) -> list[str]:
    """Check if expected params are present in actual tool call arguments."""
    errors = []
    for key, expected_val in expected.items():
        actual_val = actual_args.get(key)
        if actual_val is None:
            errors.append(f"  missing: {key} (expected {expected_val!r})")
        elif isinstance(expected_val, dict):
            # Nested check (e.g. period)
            if not isinstance(actual_val, dict):
                errors.append(f"  {key}: expected dict, got {type(actual_val).__name__}")
            else:
                for k2, v2 in expected_val.items():
                    if actual_val.get(k2) != v2:
                        errors.append(f"  {key}.{k2}: expected {v2!r}, got {actual_val.get(k2)!r}")
        elif actual_val != expected_val:
            errors.append(f"  {key}: expected {expected_val!r}, got {actual_val!r}")
    return errors


async def run_calibration(model: str, base_url: str, api_key: str, verbose: bool = False):
    """Run all test cases and print results."""
    test_register = load_test_register()

    print(f"Model: {model}")
    print(f"API URL: {base_url}")
    print(f"Test cases: {len(TEST_CASES)}")
    print()

    if verbose:
        tools = build_tools(test_register)
        print("=== Tool Definitions ===")
        print(json.dumps(tools, ensure_ascii=False, indent=2))
        print()

    passed = 0
    failed = 0

    for i, (question, expected_tool, expected_params) in enumerate(TEST_CASES, 1):
        print(f"--- Test {i}/{len(TEST_CASES)} ---")
        print(f"  Q: {question}")
        print(f"  Expected: {expected_tool}")

        result = await call_with_tools(
            question, test_register,
            model=model, base_url=base_url, api_key=api_key,
        )

        actual_tool = result.get("tool")
        actual_args = result.get("args", {})
        error = result.get("error")

        if error:
            print(f"  ERROR: {error}")
            if verbose:
                raw = result.get("raw_response", {})
                if isinstance(raw, dict):
                    choices = raw.get("choices", [{}])
                    if choices:
                        msg = choices[0].get("message", {})
                        print(f"  Raw content: {msg.get('content', '')[:300]}")
            failed += 1
            print()
            continue

        print(f"  Actual:   {actual_tool}")
        print(f"  Args:     {json.dumps(actual_args, ensure_ascii=False)}")

        # Check tool selection
        tool_ok = actual_tool == expected_tool
        if not tool_ok:
            print(f"  FAIL: wrong tool (expected {expected_tool}, got {actual_tool})")

        # Check params
        param_errors = check_params(expected_params, actual_args)
        if param_errors:
            print(f"  FAIL: param mismatches:")
            for e in param_errors:
                print(f"    {e}")

        if tool_ok and not param_errors:
            print(f"  PASS")
            passed += 1
        else:
            failed += 1

        # Show normalized params
        if verbose:
            params = result.get("params", {})
            print(f"  Normalized: {json.dumps(params, ensure_ascii=False)}")

        print()

    print(f"=== Results: {passed}/{passed + failed} passed ===")
    if failed:
        print(f"    {failed} failed")

    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Calibrate Gemma 4 E2B tool calling")
    parser.add_argument("--model", default="gemma4:e2b", help="Model name")
    parser.add_argument("--url", default="http://localhost:3000", help="Open WebUI base URL")
    parser.add_argument("--api-key", default="", help="API key for Open WebUI")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show tool definitions and raw responses")
    args = parser.parse_args()

    success = asyncio.run(run_calibration(args.model, args.url, args.api_key, args.verbose))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
