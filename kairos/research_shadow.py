"""Opt-in Codex Python SDK research assessments; never imported by an executor.

Default: prepare requests offline. Inference requires a separately authorized cap.
The SDK manages its app-server and ChatGPT authentication; no manual codex exec.
"""

import argparse
import asyncio
import hashlib
import importlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from kairos.research_assessment import (
    BASE_INSTRUCTIONS,
    SCHEMA,
    VERSION,
    digest,
    encoded,
    instructions,
    validate_answer,
    validate_snapshot,
)

SDK_VERSION = "0.162.1"
DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apply_patch_freeform",
    "apps",
    "plugins",
    "hooks",
    "codex_hooks",
    "plugin_hooks",
    "multi_agent",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "image_generation",
    "code_mode",
    "code_mode_host",
    "js_repl",
    "skill_search",
    "skill_mcp_dependency_install",
    "view_image",
    "memory_tool",
    "memories",
    "search_tool",
    "tool_search",
)


def environment():
    # SDK env overlays the process environment, so blank other inherited values.
    allowed = {"PATH", "HOME", "LANG", "XDG_RUNTIME_DIR"}
    return {k: v if k in allowed else "" for k, v in os.environ.items()}


def sdk_config(sdk, work):
    overrides = (
        'forced_login_method="chatgpt"',
        'model_provider="openai"',
        'web_search="disabled"',
        "mcp_servers={}",
        "project_doc_max_bytes=0",
        "features.skip_host_skill_discovery=true",
        *(f"features.{name}=false" for name in DISABLED_FEATURES),
    )
    return sdk.CodexConfig(cwd=str(work), env=environment(), config_overrides=overrides)


def load_sdk():
    sdk = importlib.import_module("openai_codex")
    if sdk.__version__ != SDK_VERSION:
        raise ValueError(f"Install the pinned codex-research extra ({SDK_VERSION})")
    return sdk


def write_json(path, data):
    path.write_bytes(encoded(data) + b"\n")
    path.chmod(0o600)


def validate_turn(result):
    if getattr(result.status, "value", result.status) != "completed" or result.error:
        raise ValueError("SDK turn failed/incomplete")
    items = [item.model_dump(mode="json") for item in result.items]
    if any(item.get("type") not in ("agentMessage", "reasoning") for item in items):
        raise ValueError("Unexpected tool/action item; assessment batch stopped")
    if not result.final_response:
        raise ValueError("Missing/refused final response")
    return items


async def assess(
    snapshots,
    output_dir,
    *,
    model="gpt-6.1-sol",
    effort="high",
    max_invocations=1,
    approve_subscription_usage=False,
):
    """Prepare offline by default; one SDK client, isolated thread per request."""
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 32:
        raise ValueError("Supply 1–32 predeclared arming snapshots")
    if type(max_invocations) is not int or not 1 <= max_invocations <= 32:
        raise ValueError("Invocation cap must be 1–32")
    if len(snapshots) > max_invocations:
        raise ValueError("Snapshot batch exceeds cap; do not silently select a subset")
    if model not in ("gpt-6-luna", "gpt-6.1-sol") or effort not in ("low", "medium", "high"):
        raise ValueError("Unsupported model/effort")
    for s in snapshots:
        validate_snapshot(s)
    if len({(s["setup"], s["minutes"], s["candidate_id"]) for s in snapshots}) != len(snapshots):
        raise ValueError("Duplicate candidates are not new observations")
    snapshots = json.loads(encoded(snapshots))
    output_dir = Path(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    work = output_dir.resolve() / "empty-workspace"
    work.mkdir(mode=0o700)
    requests = [{"input_sha256": digest(s), "snapshot": s} for s in snapshots]
    source = await asyncio.to_thread(Path(__file__).read_bytes)
    contract = await asyncio.to_thread(
        Path(__file__).with_name("research_assessment.py").read_bytes
    )
    declaration = {
        "declared_at": datetime.now(UTC).isoformat(),
        "contract": VERSION,
        "transport": "official openai-codex Python SDK / managed app-server JSON-RPC",
        "sdk_version": SDK_VERSION,
        "authentication": "ChatGPT subscription only",
        "model_alias": model,
        "reasoning_effort": effort,
        "max_invocations": max_invocations,
        "inference_approved": approve_subscription_usage,
        "requests": requests,
        "base_instructions": BASE_INSTRUCTIONS,
        "developer_instructions": [instructions(s) for s in snapshots],
        "schema": SCHEMA,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "contract_source_sha256": hashlib.sha256(contract).hexdigest(),
        "financial_effect": "none; permit is not order permission",
    }
    write_json(output_dir / "declaration.json", declaration)
    if not approve_subscription_usage:
        summary = {"status": "prepared_no_inference", "invocations": 0, "financial_effect": "none"}
        write_json(output_dir / "summary.json", summary)
        return summary
    sdk = load_sdk()
    results = []
    # No login, token reading, retry helper, alternate provider or billing fallback.
    async with asyncio.timeout(20):
        client = sdk.AsyncCodex(sdk_config(sdk, work))
        await client.__aenter__()
    try:
        async with asyncio.timeout(20):
            account = (await client.account()).account
        if account is None or account.root.type != "chatgpt":
            raise ValueError("Existing ChatGPT sign-in required; API-key fallback prohibited")
        for i, (s, request) in enumerate(zip(snapshots, requests, strict=True)):
            result = {
                "candidate_id": s["candidate_id"],
                "input_sha256": digest(s),
                "started_at": datetime.now(UTC).isoformat(),
            }
            handle = None
            try:
                async with asyncio.timeout(120):
                    thread = await client.thread_start(
                        model=model,
                        model_provider="openai",
                        cwd=str(work),
                        ephemeral=True,
                        sandbox=sdk.Sandbox.read_only,
                        approval_mode=sdk.ApprovalMode.deny_all,
                        base_instructions=BASE_INSTRUCTIONS,
                        developer_instructions=instructions(s),
                    )
                    handle = await thread.turn(
                        encoded(request).decode(),
                        effort=effort,
                        output_schema=SCHEMA,
                        sandbox=sdk.Sandbox.read_only,
                        approval_mode=sdk.ApprovalMode.deny_all,
                    )
                    turn = await handle.run()
                raw = {
                    "id": turn.id,
                    "status": getattr(turn.status, "value", turn.status),
                    "final_response": turn.final_response,
                    "items": [item.model_dump(mode="json") for item in turn.items],
                    "usage": turn.usage.model_dump(mode="json") if turn.usage else None,
                    "error": turn.error.model_dump(mode="json") if turn.error else None,
                }
                write_json(output_dir / f"{i:02d}-turn.json", raw)
                validate_turn(turn)
                result.update(
                    status="complete",
                    answer=validate_answer(json.loads(turn.final_response), s),
                    usage=raw["usage"],
                )
            except (
                ValueError,
                OSError,
                TimeoutError,
                sdk.CodexError,
                asyncio.CancelledError,
            ) as exc:
                if isinstance(exc, (TimeoutError, asyncio.CancelledError)) and handle is not None:
                    try:
                        async with asyncio.timeout(5):
                            await handle.interrupt()
                        result["interruption_requested"] = True
                    except (TimeoutError, OSError, sdk.CodexError) as stop_error:
                        result["interruption_error"] = str(stop_error)
                result.update(status="failed", error=str(exc) or type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError):
                    result["received_at"] = datetime.now(UTC).isoformat()
                    write_json(output_dir / "results.json", [*results, result])
                    raise
            result["received_at"] = datetime.now(UTC).isoformat()
            results.append(result)
            write_json(output_dir / "results.json", results)
            if result["status"] != "complete":
                break
    finally:
        await client.close()
    summary = {
        "status": "complete"
        if len(results) == len(snapshots) and all(r["status"] == "complete" for r in results)
        else "stopped",
        "invocations": len(results),
        "completed": sum(r["status"] == "complete" for r in results),
        "financial_effect": "none",
        "interpretation": "Thesis critique and second opinion, not measured trading value",
    }
    write_json(output_dir / "summary.json", summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--snapshots", type=Path, required=True, help="JSON list of arming-time snapshots"
    )
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--max-invocations", type=int, default=1)
    p.add_argument("--model", choices=("gpt-6-luna", "gpt-6.1-sol"), default="gpt-6.1-sol")
    p.add_argument("--effort", choices=("low", "medium", "high"), default="high")
    p.add_argument("--approve-subscription-usage", action="store_true")
    args = p.parse_args()
    summary = asyncio.run(
        assess(
            json.loads(args.snapshots.read_text()),
            args.output_dir,
            model=args.model,
            effort=args.effort,
            max_invocations=args.max_invocations,
            approve_subscription_usage=args.approve_subscription_usage,
        )
    )
    print(json.dumps(summary))
    if summary["status"] == "stopped":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
