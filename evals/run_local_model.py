"""Opt-in, synthetic development eval; never downloads models or calls a paid judge."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from icart.adapters.ollama import OllamaModel
from icart.application.interpretation import ModelFailure, interpret
from icart.application.security import PolicyViolation
from icart.bootstrap import build_local_service
from icart.domain.models import Principal, ValidationError
from icart.interfaces.presentation import render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11435")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    dataset = root / "evals/local_intent.json"
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        dataset, root / "src/icart/adapters/ollama.py", root / "src/icart/application/interpretation.py")}
    model = OllamaModel(args.base_url)
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "kind": "development-smoke",
              "hashes": hashes, "cases": []}
    try:
        report["model"] = model.verify()
    except ModelFailure:
        report["runtime_error"] = "local model verification failed"
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        return 1
    principal = Principal("synthetic-eval-user")
    for case in json.loads(dataset.read_text()):
        model.metrics.clear()
        row = {"id": case["id"], "passed": False}
        print("Evaluating " + case["id"], file=sys.stderr, flush=True)
        try:
            now = datetime.now(timezone.utc)
            result = interpret(case["request"], model, principal, now)
            row.update(draft=result.draft, questions=list(result.questions))
            expected = case["expected"]
            if "items" in expected:
                mission = result.mission
                checks = {"complete": mission is not None}
                if mission is not None:
                    checks["items"] = sorted([i.product_key, i.quantity, i.unit] for i in mission.items) == sorted(expected["items"])
                    for field in ("budget_cents", "postal_code", "fulfillment", "within_hours"):
                        checks[field] = result.draft[field] == expected[field]
                    service, registry = build_local_service(model)
                    planned = service.run(principal, mission, datetime.now(timezone.utc))
                    presentation = render(principal, mission, planned, registry, datetime.now(timezone.utc))
                    row["plan"] = presentation
                    checks["grounded_total"] = planned.plan.total_cents == expected["total_cents"]
                    checks["ready"] = planned.plan.status == "ready"
                row["checks"] = checks
                row["passed"] = all(checks.values())
            else:
                checks = {"blocked": result.mission is None, "clarification": bool(result.questions)}
                if "null_field" in expected:
                    checks["missing_preserved"] = result.draft[expected["null_field"]] is None
                if expected.get("unknown_quantity"):
                    checks["missing_preserved"] = any(i["quantity"] is None for i in result.draft["items"])
                if "constraint_contains" in expected:
                    checks["restriction_preserved"] = expected["constraint_contains"] in " ".join(result.draft["unsupported_constraints"]).lower()
                row["checks"] = checks
                row["passed"] = all(checks.values())
        except (ModelFailure, ValidationError, PolicyViolation) as exc:
            row["error_type"] = type(exc).__name__
        row["metrics"] = list(model.metrics)
        report["cases"].append(row)
        # Save after each case so interrupted runs remain visible as partial evidence.
        report["passed"] = sum(item["passed"] for item in report["cases"])
        report["completed"] = len(report["cases"])
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    return 0 if report["passed"] == report["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
