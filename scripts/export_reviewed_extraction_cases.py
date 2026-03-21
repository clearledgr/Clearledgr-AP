#!/usr/bin/env python3
"""Export reviewed production extraction truth into a deterministic local corpus file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from clearledgr.services.correction_learning import CorrectionLearningService


DEFAULT_OUTPUT_PATH = Path("tests/fixtures/reviewed_production_invoice_truth.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export reviewed production extraction cases")
    parser.add_argument(
        "--organization",
        default="default",
        help="Organization id to export from the correction learning store.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Destination JSON path for the reviewed production corpus.",
    )
    args = parser.parse_args()

    service = CorrectionLearningService(args.organization)
    result = service.export_reviewed_extraction_cases(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
