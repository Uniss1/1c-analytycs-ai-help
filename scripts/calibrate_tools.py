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
from api.param_validator import validate as validate_tool_params

DB_PATH = str(Path(__file__).parent.parent / "metadata.db")

# Self-healing loop (mirrors api.main.MAX_VALIDATION_RETRIES)
MAX_VALIDATION_RETRIES = 3


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

# --- Test cases: (question, expected_mode, expected_params_subset) ---
# All cases use single "query" tool. expected_mode is what call_with_tools
# returns as "tool" (= the mode from model's args).
# expected_params checked against raw model args (Latin keys + mode).

TEST_CASES = [
    # Aggregate — basic
    (
        "Какая выручка за март 2025?",
        "aggregate",
        {"mode": "aggregate", "resource": "Сумма", "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Сколько EBITDA по факту за январь 2025?",
        "aggregate",
        {"mode": "aggregate", "metric": "EBITDA", "scenario": "Факт", "year": 2025, "month": 1},
    ),
    (
        "Прогноз выручки на декабрь 2025",
        "aggregate",
        {"mode": "aggregate", "scenario": "Прогноз", "metric": "Выручка", "year": 2025, "month": 12},
    ),
    (
        "План по ОЗП на февраль 2025 для ДЗО-1",
        "aggregate",
        {"mode": "aggregate", "scenario": "План", "metric": "ОЗП", "company": "ДЗО-1", "year": 2025, "month": 2},
    ),

    # Group by
    (
        "Выручка по ДЗО за март 2025",
        "group_by",
        {"mode": "group_by", "group_by": "company", "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Маржа в разрезе показателей за январь 2025",
        "group_by",
        {"mode": "group_by", "group_by": "metric", "year": 2025, "month": 1},
    ),
    (
        "Факт по сценариям за март 2025",
        "group_by",
        {"mode": "group_by", "group_by": "scenario", "year": 2025, "month": 3},
    ),

    # Compare
    (
        "Сравни факт и план по выручке за март 2025",
        "compare",
        {"mode": "compare", "compare_by": "scenario", "compare_values": ["Факт", "План"],
         "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Факт vs бюджет EBITDA за январь 2025",
        "compare",
        {"mode": "compare", "compare_by": "scenario", "metric": "EBITDA", "year": 2025, "month": 1},
    ),

    # Edge cases
    (
        "Какая выручка?",
        "aggregate",
        {"mode": "aggregate", "metric": "Выручка"},
    ),
    (
        "Сколько заработали в марте 2025?",
        "aggregate",
        {"mode": "aggregate", "year": 2025, "month": 3},
    ),
    (
        "Маржа по всем ДЗО за февраль 2025",
        "group_by",
        {"mode": "group_by", "metric": "Маржа", "group_by": "company", "year": 2025, "month": 2},
    ),
]

# --- Degraded cases: input that should trigger validation failure + auto-recovery ---
# Here we don't assert specific args — only that the self-healing loop converges
# within MAX_VALIDATION_RETRIES attempts (i.e. validation.ok at the end).
DEGRADED_CASES: list[tuple[str, str]] = [
    ("Выручка по Газпром за март 2025", "unknown company name → should retry with valid ДЗО or no-op company"),
    ("Выручка за 1999 год", "year outside 2020-2030 → should retry with year in range"),
    ("Какой CAPEX за март 2025", "CAPEX not in metric enum → should retry with valid metric"),
    ("Выручка по сценарию 'Скорректированный факт' за март 2025", "invalid scenario → retry"),
    ("Маржа по ДЗО Роснефть за март 2025", "unknown company 'Роснефть' → retry"),
    ("Выручка за месяц 13 2025 года", "month=13 out of range → retry with valid month"),
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


async def _call_with_self_healing(
    question: str, register: dict, *, model: str, base_url: str, api_key: str,
) -> tuple[dict, int, list[list[str]]]:
    """Mirror of api/main.py self-healing loop for calibration.

    Returns (final_result, attempts, per_attempt_validation_errors).
    """
    feedback: str | None = None
    errors_per_attempt: list[list[str]] = []
    result: dict = {}
    for attempt in range(1, MAX_VALIDATION_RETRIES + 1):
        result = await call_with_tools(
            question, register,
            model=model, base_url=base_url, api_key=api_key,
            validation_feedback=feedback,
        )
        if not result.get("tool"):
            errors_per_attempt.append(["no tool call"])
            return result, attempt, errors_per_attempt
        params = result.get("params", {})
        if params.get("needs_clarification"):
            errors_per_attempt.append(["needs_clarification"])
            return result, attempt, errors_per_attempt
        validation = validate_tool_params(result, register)
        if validation.ok:
            errors_per_attempt.append([])
            return result, attempt, errors_per_attempt
        errors_per_attempt.append(list(validation.errors))
        feedback = (
            f"Previous tool args: {json.dumps(result.get('args', {}), ensure_ascii=False)}\n"
            f"Validation errors:\n" + "\n".join(f"- {e}" for e in validation.errors)
        )
    return result, MAX_VALIDATION_RETRIES, errors_per_attempt


async def run_calibration(model: str, base_url: str, api_key: str, verbose: bool = False):
    """Run all test cases and print results."""
    test_register = load_test_register()

    total_cases = len(TEST_CASES) + len(DEGRADED_CASES)
    print(f"Model: {model}")
    print(f"API URL: {base_url}")
    print(f"Test cases: {len(TEST_CASES)} base + {len(DEGRADED_CASES)} degraded = {total_cases}")
    print()

    if verbose:
        tools = build_tools(test_register)
        print("=== Tool Definitions ===")
        print(json.dumps(tools, ensure_ascii=False, indent=2))
        print()

    passed = 0
    failed = 0

    for i, (question, expected_tool, expected_params) in enumerate(TEST_CASES, 1):
        print(f"--- Test {i}/{len(TEST_CASES)} (base) ---")
        print(f"  Q: {question}")
        print(f"  Expected: {expected_tool}")

        result, attempts, err_log = await _call_with_self_healing(
            question, test_register,
            model=model, base_url=base_url, api_key=api_key,
        )
        if attempts > 1:
            print(f"  Self-healing attempts: {attempts}")
            for a_idx, errs in enumerate(err_log[:-1], 1):
                print(f"    attempt {a_idx} errors: {errs}")

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

    # --- Degraded cases: only check self-healing converges ---
    deg_passed = 0
    deg_failed = 0
    for j, (question, note) in enumerate(DEGRADED_CASES, 1):
        print(f"--- Degraded {j}/{len(DEGRADED_CASES)} ---")
        print(f"  Q: {question}")
        print(f"  Note: {note}")

        result, attempts, err_log = await _call_with_self_healing(
            question, test_register,
            model=model, base_url=base_url, api_key=api_key,
        )
        for a_idx, errs in enumerate(err_log[:-1], 1):
            print(f"    attempt {a_idx} errors: {errs}")
        final_errs = err_log[-1] if err_log else ["unknown"]

        actual_args = result.get("args", {})
        print(f"  Final args: {json.dumps(actual_args, ensure_ascii=False)}")
        print(f"  Attempts:   {attempts}")

        params = result.get("params", {})
        if final_errs == [] and not params.get("needs_clarification"):
            print(f"  PASS (auto-recovered in {attempts} attempt(s))")
            deg_passed += 1
        else:
            print(f"  FAIL (final errors: {final_errs})")
            deg_failed += 1
        print()

    base_total = passed + failed
    deg_total = deg_passed + deg_failed
    grand_total = base_total + deg_total
    grand_passed = passed + deg_passed
    print(f"=== Base cases: {passed}/{base_total} passed ===")
    print(f"=== Degraded cases: {deg_passed}/{deg_total} auto-recovered ===")
    print(f"=== Overall: {grand_passed}/{grand_total} ===")

    return failed == 0 and deg_failed == 0


def main():
    parser = argparse.ArgumentParser(description="Calibrate Gemma 4 E2B tool calling")
    parser.add_argument("--model", default="gemma4:e2b", help="Model name")
    parser.add_argument("--url", default="http://localhost:11434", help="Ollama or OpenAI-compatible base URL")
    parser.add_argument("--api-key", default="", help="API key for Open WebUI")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show tool definitions and raw responses")
    args = parser.parse_args()

    success = asyncio.run(run_calibration(args.model, args.url, args.api_key, args.verbose))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
