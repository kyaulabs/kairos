"""Opt-in, quota-capped historical annotations through the official Codex OAuth client.

Never called by an executor. Responses cannot authorize orders or change a replay.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from kairos.domain import dec

SCHEMA = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": ["trend", "range", "unclear"]},
        "quality": {"type": "string", "enum": ["weak", "mixed", "strong"]},
        "reason": {"type": "string"},
    },
    "required": ["regime", "quality", "reason"],
    "additionalProperties": False,
}
PROMPT = """Classify this historical BTC/USDT long-only candidate using only the supplied facts.
Use no tools, files, web search, remembered prices or subsequent events. Candle-derived VWAP
and sigma, fees, IOC costs and liquidity are approximations, not historical OKX evidence.
Regime: trend, range or unclear. Setup quality after assumed costs: weak, mixed or strong.
A positive target projection is not expectancy. Give a brief reason, not trading instructions.
This is retrospective shadow annotation, never order permission or a backdated decision.
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def payload(case, candidate):
    """Explicit known-at whitelist: no fill/outcome, size, balance or exit evidence."""
    fields = (
        "armed_at",
        "arm_clock",
        "arming_facts",
        "level_context",
        "confirmed_at",
        "confirmation_clock",
        "confirmation_bars",
        "confirmation_reference",
        "confirmation_stop",
        "confirmation_target",
        "confirmation_stop_widening",
        "next_open_at",
        "next_open_mid",
        "next_open_entry_bound",
        "previous_minute_present",
        "previous_minute_volume",
    )
    result = {"setup": case["policy"], "minutes": case["minutes"]}
    result.update({k: candidate[k] for k in fields if k in candidate})
    economics = candidate.get("entry_economics", {})
    result["modeled_economics"] = {
        k: economics[k]
        for k in (
            "net_target_reward_per_gross_unit",
            "net_stop_loss_per_gross_unit",
            "net_planned_R",
            "cost_share_of_gross_target_room",
        )
        if k in economics
    }
    result["knowledge_time"] = candidate.get("next_open_at", candidate["confirmed_at"])
    return result


def samples(report):
    """First confirmed candidate, then first on/after March 1; no outcome selection."""
    selected = []
    for case in sorted(
        (c for c in report["cases"] if c.get("signal_version") == "v2"),
        key=lambda c: (c["minutes"], c["policy"]),
    ):
        rows = sorted(
            (
                r
                for r in case["diagnostics"]["candidates"]
                if "confirmed_at" in r and not r.get("warmup_arm")
            ),
            key=lambda r: r["confirmed_at"],
        )
        used = set()
        for boundary in (1706745600, 1709251200):
            row = next(
                (
                    r
                    for r in rows
                    if r["confirmed_at"] >= boundary and r["candidate_id"] not in used
                ),
                None,
            )
            if row:
                used.add(row["candidate_id"])
                selected.append(
                    {
                        "candidate_id": row["candidate_id"],
                        "case": f"{case['policy']}:{case['minutes']}",
                        "input": payload(case, row),
                    }
                )
    return selected


def command(client, schema, output):
    args = [
        client,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--json",
        "--color",
        "never",
        "--model",
        "gpt-5.4",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output),
        "-c",
        'forced_login_method="chatgpt"',
        "-c",
        'model_provider="openai"',
        "-c",
        'approval_policy="never"',
        "-c",
        'web_search="disabled"',
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        "mcp_servers={}",
    ]
    for feature in (
        "shell_tool",
        "unified_exec",
        "apps",
        "plugins",
        "multi_agent",
        "browser_use",
        "browser_use_external",
        "computer_use",
        "image_generation",
        "code_mode_host",
        "skill_search",
        "skill_mcp_dependency_install",
        "view_image",
    ):
        args.extend(("--disable", feature))
    return args + ["-"]


def environment():
    # Only the official client accesses its existing credential store; no token copying.
    return {
        k: os.environ[k] for k in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR") if k in os.environ
    }


def validate_answer(answer):
    if not isinstance(answer, dict) or set(answer) != set(SCHEMA["required"]):
        raise ValueError("Unexpected shadow response schema")
    for field in ("regime", "quality"):
        if answer[field] not in SCHEMA["properties"][field]["enum"]:
            raise ValueError("Unexpected shadow response category")
    if not isinstance(answer["reason"], str) or not 1 <= len(answer["reason"]) <= 1000:
        raise ValueError("Invalid shadow reason")
    return answer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--approve-subscription-usage", action="store_true")
    args = p.parse_args()
    if not args.approve_subscription_usage:
        p.error("Explicit subscription usage approval required; disabled by default")
    client = shutil.which("codex")
    if not client:
        p.error("Official Codex CLI required")
    env = environment()
    auth = subprocess.run([client, "login", "status"], env=env, capture_output=True, timeout=20)
    if auth.returncode or "Logged in using ChatGPT" not in (auth.stdout + auth.stderr).decode():
        p.error("ChatGPT login required; API-key fallback is prohibited")
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    report_bytes = args.report.read_bytes()
    report = json.loads(report_bytes)
    assert report["status"] == "complete"
    inputs = samples(report)
    assert len(inputs) <= 32
    schema = args.output_dir.resolve() / "schema.json"
    schema.write_text(json.dumps(SCHEMA, indent=2) + "\n")
    declaration = {
        "declared_at": datetime.now(UTC).isoformat(),
        "max_invocations": 32,
        "client_version": subprocess.check_output([client, "--version"], env=env).decode().strip(),
        "model_alias": "gpt-5.4",
        "authentication": "official Codex ChatGPT sign-in",
        "report_sha256": digest(report_bytes),
        "source_sha256": digest(Path(__file__).read_bytes()),
        "prompt": PROMPT,
        "schema": SCHEMA,
        "samples": inputs,
        "selection": "first confirmed candidate and first on/after March 1 per independent case; no outcome filter",
        "limitations": "Retrospective, reused development prices and possible model pretraining contamination. No backdated decisions, trading effect, expectancy or alpha claim. Alias is not an immutable model snapshot.",
    }
    (args.output_dir / "declaration.json").write_text(json.dumps(declaration, indent=2) + "\n")
    work = args.output_dir / "empty-workspace"
    work.mkdir(mode=0o700)
    results = []
    for i, sample in enumerate(inputs):
        output = args.output_dir.resolve() / f"{i:02d}-answer.json"
        started = datetime.now(UTC).isoformat()
        result = {
            "candidate_id": sample["candidate_id"],
            "case": sample["case"],
            "started_at": started,
        }
        try:
            run = subprocess.run(
                command(client, schema, output),
                cwd=work,
                env=env,
                input=PROMPT + json.dumps(sample["input"], sort_keys=True),
                capture_output=True,
                text=True,
                timeout=120,
            )
            (args.output_dir / f"{i:02d}-events.jsonl").write_text(run.stdout)
            (args.output_dir / f"{i:02d}-client.log").write_text(run.stderr)
            if run.returncode:
                raise ValueError(f"Codex exit {run.returncode}; no retry or fallback")
            events = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
            for event in events:
                if event.get("type") in ("turn.failed", "error"):
                    raise ValueError("Client reported failed/incomplete response")
                item = event.get("item")
                if item and item.get("type") not in ("agent_message", "reasoning"):
                    raise ValueError("Unexpected tool event; shadow batch stopped")
            result.update(
                answer=validate_answer(json.loads(output.read_text())),
                usage=[e.get("usage") for e in events if e.get("type") == "turn.completed"],
                status="complete",
            )
            facts = sample["input"].get("arming_facts", {})
            result["deterministic_regime"] = (
                "trend" if facts.get("regime") in ("rising", "falling") else "range"
            )
            r = sample["input"]["modeled_economics"].get("net_planned_R")
            result["deterministic_room_quality"] = (
                "strong" if r is not None and dec(r) >= 1 else "weak"
            )
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                for suffix, data in (("events.jsonl", exc.stdout), ("client.log", exc.stderr)):
                    if data is not None:
                        text = data.decode(errors="replace") if isinstance(data, bytes) else data
                        (args.output_dir / f"{i:02d}-{suffix}").write_text(text)
            result.update(status="failed", error=str(exc))
        result["received_at"] = datetime.now(UTC).isoformat()
        results.append(result)
        (args.output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        if result["status"] != "complete":
            break
    completed = [r for r in results if r["status"] == "complete"]
    summary = {
        "invocations": len(results),
        "completed": len(completed),
        "financial_effect": "none",
        "regime_agreement": sum(
            r["answer"]["regime"] == r["deterministic_regime"] for r in completed
        ),
        "room_quality_agreement": sum(
            r["answer"]["quality"] == r["deterministic_room_quality"] for r in completed
        ),
        "interpretation": "Agreement is not accuracy; room R is not expected R. No model veto selected.",
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
