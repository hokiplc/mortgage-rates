#!/usr/bin/env python3
"""Build a backup best-rates JSON file from lender rate files.

This script is Python-native so it can be used in environments where the
TypeScript/Bun scraper is not available. It reads already-scraped lender JSON
files from data/rates/, computes the lowest rates by segment, and writes a
bestrates JSON artifact for comparison/backup workflows.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXCLUDED_FILES = {"updates.txt", "bestrates.json", "bestrate360.json"}


@dataclass
class FlatRate:
    id: str
    lenderId: str
    name: str
    type: str
    rate: float
    apr: float | None
    fixedTerm: int | None
    minLtv: float | None
    maxLtv: float | None
    buyerTypes: list[str]


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_rates_dir = (script_dir / "../../data/rates").resolve()
    default_output = (script_dir / "../../data/rates/bestrates.json").resolve()

    parser = argparse.ArgumentParser(
        description="Build best-rates backup JSON from lender rate files",
    )
    parser.add_argument(
        "--rates-dir",
        type=Path,
        default=default_rates_dir,
        help=f"Directory containing lender rate files (default: {default_rates_dir})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"Output file path for bestrates JSON (default: {default_output})",
    )
    return parser.parse_args()


def load_rates_from_file(file_path: Path) -> list[dict[str, Any]]:
    data = json.loads(file_path.read_text(encoding="utf-8"))

    # Backward compatibility with legacy array format.
    if isinstance(data, list):
        return data

    if isinstance(data, dict) and isinstance(data.get("rates"), list):
        return data["rates"]

    raise ValueError(f"Unsupported rate file format: {file_path}")


def to_flat_rate(raw: dict[str, Any]) -> FlatRate | None:
    try:
        return FlatRate(
            id=str(raw["id"]),
            lenderId=str(raw["lenderId"]),
            name=str(raw.get("name", "")),
            type=str(raw["type"]),
            rate=float(raw["rate"]),
            apr=float(raw["apr"]) if raw.get("apr") is not None else None,
            fixedTerm=int(raw["fixedTerm"]) if raw.get("fixedTerm") is not None else None,
            minLtv=float(raw["minLtv"]) if raw.get("minLtv") is not None else None,
            maxLtv=float(raw["maxLtv"]) if raw.get("maxLtv") is not None else None,
            buyerTypes=[str(v) for v in raw.get("buyerTypes", [])],
        )
    except (KeyError, TypeError, ValueError):
        return None


def pick_best(rates: list[FlatRate]) -> FlatRate | None:
    if not rates:
        return None

    # Sort by primary rate, then APR (if available), then lender/name for stability.
    return sorted(
        rates,
        key=lambda r: (
            r.rate,
            float("inf") if r.apr is None else r.apr,
            r.lenderId,
            r.name,
        ),
    )[0]


def build_best_rates(flat_rates: list[FlatRate]) -> dict[str, Any]:
    by_buyer_and_type: dict[tuple[str, str], list[FlatRate]] = defaultdict(list)
    by_fixed_term: dict[tuple[str, int], list[FlatRate]] = defaultdict(list)

    for rate in flat_rates:
        for buyer_type in rate.buyerTypes:
            by_buyer_and_type[(buyer_type, rate.type)].append(rate)
            if rate.type == "fixed" and rate.fixedTerm is not None:
                by_fixed_term[(buyer_type, rate.fixedTerm)].append(rate)

    best_by_buyer_and_type: list[dict[str, Any]] = []
    for (buyer_type, rate_type), values in sorted(by_buyer_and_type.items()):
        best = pick_best(values)
        if best:
            best_by_buyer_and_type.append(
                {
                    "buyerType": buyer_type,
                    "rateType": rate_type,
                    "best": asdict(best),
                    "comparedCount": len(values),
                }
            )

    best_fixed_by_term: list[dict[str, Any]] = []
    for (buyer_type, term), values in sorted(by_fixed_term.items()):
        best = pick_best(values)
        if best:
            best_fixed_by_term.append(
                {
                    "buyerType": buyer_type,
                    "fixedTerm": term,
                    "best": asdict(best),
                    "comparedCount": len(values),
                }
            )

    return {
        "bestByBuyerTypeAndRateType": best_by_buyer_and_type,
        "bestFixedByTerm": best_fixed_by_term,
    }


def main() -> int:
    args = parse_args()
    rates_dir = args.rates_dir.resolve()
    output_path = args.output.resolve()

    if not rates_dir.exists():
        print(f"Rates directory not found: {rates_dir}")
        print("Run the scraper first or pass --rates-dir to an existing rates folder.")
        return 1

    lender_files = sorted(
        p
        for p in rates_dir.glob("*.json")
        if p.name not in EXCLUDED_FILES and p.is_file()
    )

    all_flat_rates: list[FlatRate] = []
    invalid_rows = 0

    for lender_file in lender_files:
        try:
            rates = load_rates_from_file(lender_file)
        except Exception:
            continue

        for raw_rate in rates:
            flat = to_flat_rate(raw_rate)
            if flat:
                all_flat_rates.append(flat)
            else:
                invalid_rows += 1

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "ratesDir": str(rates_dir),
            "fileCount": len(lender_files),
            "rateCount": len(all_flat_rates),
            "invalidRows": invalid_rows,
        },
        "segments": build_best_rates(all_flat_rates),
        "allRates": [asdict(rate) for rate in all_flat_rates],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Built backup best-rates JSON: {output_path}")
    print(f"Files scanned: {len(lender_files)}")
    print(f"Rates loaded: {len(all_flat_rates)}")
    if invalid_rows:
        print(f"Skipped invalid rows: {invalid_rows}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
