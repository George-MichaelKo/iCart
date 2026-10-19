"""Natural-language local experiment; optional comparison, never persistence or payment."""

import argparse
from datetime import datetime, timezone
import json

from icart.adapters.ollama import OllamaModel
from icart.application.interpretation import ModelFailure, interpret
from icart.application.security import PolicyViolation
from icart.bootstrap import build_local_service
from icart.domain.models import Principal, ValidationError
from icart.interfaces.presentation import as_text, display_text, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interpret a shopping request with a local Ollama model")
    parser.add_argument("request")
    parser.add_argument("--plan", action="store_true", help="also compare fictional offers if the draft is complete")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--base-url", default="http://127.0.0.1:11435")
    args = parser.parse_args(argv)
    try:
        model = OllamaModel(args.base_url)
        identity = model.verify()
        principal = Principal("local-demo-user")
        interpretation = interpret(args.request, model, principal, datetime.now(timezone.utc))
        output = {"model": identity, "draft": interpretation.draft,
                  "questions": list(interpretation.questions), "plan": None, "metrics": model.metrics}
        if args.plan and interpretation.mission is not None:
            service, registry = build_local_service(model)
            result = service.run(principal, interpretation.mission, datetime.now(timezone.utc))
            output["plan"] = render(principal, interpretation.mission, result, registry, datetime.now(timezone.utc))
        if args.json_output:
            print(json.dumps(output, indent=2, ensure_ascii=True))
        else:
            print("iCart · LOCAL MODEL EXPERIMENT · fictional stores, no purchases")
            print("Interpreted request — review for accuracy:")
            print(json.dumps(interpretation.draft, indent=2, ensure_ascii=True))
            for question in interpretation.questions:
                print(display_text(question))
            if output["plan"]:
                print(as_text(output["plan"]))
            print("Model calls:", json.dumps(model.metrics))
    except (ValidationError, PolicyViolation, ModelFailure) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
