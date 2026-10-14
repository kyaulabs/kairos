import unittest
from pathlib import Path
from unittest.mock import patch

from kairos.research_shadow import (
    command,
    environment,
    payload,
    samples,
    validate_answer,
    validate_events,
)


class ResearchShadowTests(unittest.TestCase):
    def test_payload_cannot_leak_fills_exit_results_sizes_or_balances(self):
        c = {"policy": "fib-ema", "minutes": 5}
        row = {
            "confirmed_at": 1709251200,
            "next_open_at": 1709251201,
            "outcome": "filled",
            "filled_quantity": "100",
            "requested_quantity": "100",
            "balance": "1000",
            "exit": {"profit": "100"},
            "arming_facts": {"atr": "20"},
            "entry_economics": {"net_planned_R": "0.5", "filled_projection": {"net_owned": "100"}},
        }
        a = payload(c, row)
        row.update(
            outcome="unfilled_IOC", balance="0", filled_quantity="0", exit={"profit": "-100"}
        )
        self.assertEqual(payload(c, row), a)
        self.assertNotIn("filled_projection", a["modeled_economics"])
        self.assertEqual(a["knowledge_time"], 1709251201)

    def test_sampling_uses_dates_not_future_success_or_fill_outcomes(self):
        rows = [
            {"candidate_id": str(i), "confirmed_at": t, "outcome": "filled"}
            for i, t in enumerate((1706745600, 1709251200, 1709251500))
        ]
        report = {
            "cases": [
                {
                    "policy": "fib-ema",
                    "minutes": 5,
                    "signal_version": "v2",
                    "diagnostics": {"candidates": rows},
                }
            ]
        }
        a = samples(report)
        for r in rows:
            r["outcome"] = "blocked_price_plan"
        self.assertEqual(samples(report), a)
        self.assertEqual([r["candidate_id"] for r in a], ["0", "1"])

    def test_api_credentials_are_not_inherited(self):
        with patch.dict(
            "os.environ",
            {
                "HOME": "/tmp/home",
                "PATH": "/bin",
                "OPENAI_API_KEY": "forbidden",
                "CODEX_API_KEY": "forbidden",
            },
            clear=True,
        ):
            self.assertEqual(environment(), {"HOME": "/tmp/home", "PATH": "/bin"})

    def test_official_client_is_forced_to_chatgpt_read_only_without_tools(self):
        args = command("/bin/codex", Path("schema.json"), Path("answer.json"))
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertIn('model_provider="openai"', args)
        self.assertIn('web_search="disabled"', args)
        self.assertIn("read-only", args)
        self.assertIn("--ignore-user-config", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("shell_tool", args)
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-luna")
        self.assertIn('model_reasoning_effort="low"', args)
        self.assertNotIn("--approve-for-me", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_requested_sol_high_replaces_only_the_model_and_effort(self):
        default = command("/bin/codex", Path("schema.json"), Path("answer.json"))
        requested = command(
            "/bin/codex",
            Path("schema.json"),
            Path("answer.json"),
            model="gpt-6.1-sol",
            effort="high",
        )
        self.assertEqual(requested[requested.index("--model") + 1], "gpt-6.1-sol")
        self.assertIn('model_reasoning_effort="high"', requested)
        self.assertEqual(
            [(a, b) for a, b in zip(default, requested, strict=True) if a != b],
            [
                ("gpt-6-luna", "gpt-6.1-sol"),
                ('model_reasoning_effort="low"', 'model_reasoning_effort="high"'),
            ],
        )

    def test_disabled_tool_warning_is_not_a_tool_but_actions_and_failures_stop(self):
        warning = {
            "type": "item.completed",
            "item": {"type": "error", "message": "Code mode host disabled; fail closed"},
        }
        complete = {"type": "turn.completed"}
        self.assertEqual(validate_events([warning, complete]), [warning["item"]["message"]])
        for event in (
            {"type": "error"},
            {"type": "turn.failed"},
            {"type": "item.completed", "item": {"type": "command_execution"}},
            {"type": "item.completed", "item": {"type": "mcp_tool_call"}},
        ):
            with self.assertRaises(ValueError):
                validate_events([event, complete])
        with self.assertRaises(ValueError):
            validate_events([warning])

    def test_refusal_extra_fields_and_invalid_categories_are_not_permissions(self):
        for value in (
            {"refusal": "no"},
            {"regime": "buy", "quality": "strong", "reason": "x"},
            {"regime": "trend", "quality": "strong", "reason": "x", "size": "100"},
        ):
            with self.assertRaises(ValueError):
                validate_answer(value)
        self.assertEqual(
            validate_answer({"regime": "unclear", "quality": "mixed", "reason": "Sparse facts"})[
                "regime"
            ],
            "unclear",
        )


if __name__ == "__main__":
    unittest.main()
