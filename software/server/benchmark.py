import argparse
import json
import os
import re
from pathlib import Path

from minicpmo_runner import MiniCPMOAccessibilityRunner

UNSAFE_PATTERN = re.compile(r"[零一二两三四五六七八九十百\d.]+\s*(米|厘米|cm|步|度)", re.IGNORECASE)


def main():
    parser = argparse.ArgumentParser(description="通过远程模型 API 运行场景评测")
    parser.add_argument("--config", default=os.getenv("MODEL_CONFIG_PATH", "model_profiles.json"))
    parser.add_argument("--profile", default=os.getenv("MODEL_PROFILE", ""))
    parser.add_argument("--api-url", default=os.getenv("MODEL_API_BASE_URL", ""))
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="MODEL_API_KEY")
    parser.add_argument("--model", default=os.getenv("MODEL_NAME", ""))
    parser.add_argument("--timeout", default=None, type=int)
    parser.add_argument("--cases", default="examples/cases.json")
    parser.add_argument("--output", default="benchmark_results.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    cases_path = (root / args.cases).resolve()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    runner = MiniCPMOAccessibilityRunner(
        config_path=args.config,
        profile=args.profile,
        api_url=args.api_url,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        model_name=args.model,
        timeout=args.timeout,
    )
    runner.load()

    results = []
    for case in cases:
        image_path = root / "examples" / case["image"]
        if not image_path.exists():
            results.append({**case, "status": "missing_image", "image_path": str(image_path)})
            continue

        result = runner.infer_image(str(image_path), case["question"])
        parsed = result.parsed
        speech = parsed.get("speech", "") if parsed else ""
        results.append(
            {
                **case,
                "status": result.status,
                "latency_ms": result.latency_ms,
                "profile": result.profile,
                "model": result.model,
                "parsed": parsed,
                "raw_answer": result.raw_answer,
                "checks": {
                    "json_parsed": parsed is not None,
                    "no_precise_distance": not bool(UNSAFE_PATTERN.search(speech)),
                    "direction_or_unknown": bool(parsed and parsed.get("direction")),
                    "expected_intent": bool(parsed and parsed.get("intent") == case["expected_intent"]),
                },
            }
        )

    executed = [item for item in results if item["status"] != "missing_image"]
    parsed_count = sum(bool(item.get("checks", {}).get("json_parsed")) for item in executed)
    safe_count = sum(bool(item.get("checks", {}).get("no_precise_distance")) for item in executed)
    summary = {
        "total_cases": len(results),
        "executed_cases": len(executed),
        "missing_images": len(results) - len(executed),
        "json_parse_rate": parsed_count / len(executed) if executed else 0,
        "safe_language_rate": safe_count / len(executed) if executed else 0,
        "model_gateway": runner.status(),
    }
    output = {"summary": summary, "results": results}
    output_path = (root / args.output).resolve()
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"详细结果已写入: {output_path}")


if __name__ == "__main__":
    main()