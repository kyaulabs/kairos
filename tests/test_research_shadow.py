import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kairos.research_assessment import BASE_INSTRUCTIONS, SCHEMA
from kairos.research_shadow import SDK_VERSION, assess, environment, load_sdk, validate_turn
from tests.test_research_assessment import answer, fixture


class JsonModel:
    def __init__(self, data):
        self.data = data

    def model_dump(self, **kwargs):
        return self.data


class FakeSDK:
    __version__ = SDK_VERSION
    CodexError = RuntimeError
    Sandbox = SimpleNamespace(read_only="read-only")
    ApprovalMode = SimpleNamespace(deny_all="deny_all")
    CodexConfig = SimpleNamespace

    def __init__(self, account_type="chatgpt", fail=None):
        self.account_type, self.fail = account_type, fail
        self.configs, self.starts, self.runs = [], [], []
        self.closed = 0
        self.interrupted = 0

    def AsyncCodex(self, config):
        self.configs.append(config)
        return self

    async def __aenter__(self):
        return self

    async def account(self):
        return SimpleNamespace(
            account=SimpleNamespace(root=SimpleNamespace(type=self.account_type))
        )

    async def close(self):
        self.closed += 1

    async def thread_start(self, **kwargs):
        self.starts.append(kwargs)
        return self

    async def turn(self, text, **kwargs):
        self.runs.append((text, kwargs))
        return self

    async def interrupt(self):
        self.interrupted += 1

    async def run(self):
        text = self.runs[-1][0]
        if self.fail == "timeout":
            raise TimeoutError("Synthetic deadline")
        if self.fail == "cancel":
            raise asyncio.CancelledError()
        a = answer(json.loads(text)["snapshot"])
        if self.fail == "binding":
            a["input_sha256"] = "0" * 64
        if self.fail == "refs":
            a["checks"][0]["evidence"] = ["/bars/6/close   /bars/7/close"]
        item_type = "mcpToolCall" if self.fail == "tool" else "agentMessage"
        echo = text + "tampered" if self.fail == "echo" else text
        return SimpleNamespace(
            id="offline-test",
            status="completed",
            error=None,
            final_response=json.dumps(a),
            items=[
                JsonModel(
                    {
                        "type": "userMessage",
                        "content": [{"type": "text", "text": echo, "text_elements": []}],
                    }
                ),
                JsonModel({"type": item_type}),
            ],
            usage=JsonModel({"inputTokens": 20, "outputTokens": 5}),
        )


class ResearchShadowTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_preparation_cannot_import_or_start_sdk(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch(
                "kairos.research_shadow.load_sdk",
                side_effect=AssertionError("No inference allowed"),
            ),
        ):
            out = Path(d) / "prepared"
            result = await assess([fixture()], out)
            self.assertEqual(result["invocations"], 0)
            self.assertEqual(result["status"], "prepared_no_inference")
            self.assertEqual((out / "declaration.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                await assess([fixture()], out)

    async def test_sdk_reuses_one_client_but_isolates_candidate_threads(self):
        sdk = FakeSDK()
        with (
            tempfile.TemporaryDirectory() as d,
            patch("kairos.research_shadow.load_sdk", return_value=sdk),
        ):
            out = Path(d) / "run"
            snapshots = [fixture(), fixture("fib-ema")]
            before = copy.deepcopy(snapshots)
            result = await assess(
                snapshots, out, max_invocations=2, approve_subscription_usage=True
            )
            self.assertEqual(result["completed"], 2)
            self.assertEqual(result["financial_effect"], "none")
            self.assertEqual(snapshots, before)
            self.assertEqual(len(sdk.configs), 1)
            self.assertEqual(len(sdk.starts), 2)
            self.assertEqual(sdk.closed, 1)
            for start in sdk.starts:
                self.assertTrue(start["ephemeral"])
                self.assertEqual(start["base_instructions"], BASE_INSTRUCTIONS)
                self.assertEqual(start["approval_mode"], "deny_all")
                self.assertEqual(start["sandbox"], "read-only")
                self.assertEqual(start["model_provider"], "openai")
                self.assertIn("Setup-specific questions", start["developer_instructions"])
            for _, params in sdk.runs:
                self.assertEqual(params["output_schema"], SCHEMA)
                self.assertEqual(params["effort"], "high")
            overrides = sdk.configs[0].config_overrides
            for key in (
                'forced_login_method="chatgpt"',
                "features.shell_tool=false",
                "features.hooks=false",
                "mcp_servers={}",
            ):
                self.assertIn(key, overrides)
            self.assertEqual(
                json.loads((out / "00-turn.json").read_text())["usage"]["inputTokens"], 20
            )

    async def test_same_arm_time_at_two_intervals_is_not_a_duplicate(self):
        sdk = FakeSDK()
        with (
            tempfile.TemporaryDirectory() as d,
            patch("kairos.research_shadow.load_sdk", return_value=sdk),
        ):
            result = await assess(
                [fixture(minutes=5), fixture(minutes=15)],
                Path(d) / "run",
                max_invocations=2,
                approve_subscription_usage=True,
            )
            self.assertEqual(result["completed"], 2)
            self.assertEqual(len(sdk.starts), 2)

    async def test_external_cancellation_requests_interrupt_and_closes_client(self):
        sdk = FakeSDK(fail="cancel")
        with (
            tempfile.TemporaryDirectory() as d,
            patch("kairos.research_shadow.load_sdk", return_value=sdk),
        ):
            out = Path(d) / "run"
            with self.assertRaises(asyncio.CancelledError):
                await assess([fixture()], out, approve_subscription_usage=True)
            self.assertEqual(sdk.interrupted, 1)
            self.assertEqual(sdk.closed, 1)
            self.assertTrue(
                json.loads((out / "results.json").read_text())[0]["interruption_requested"]
            )

    async def test_api_auth_is_rejected_without_any_model_turn(self):
        sdk = FakeSDK(account_type="apiKey")
        with (
            tempfile.TemporaryDirectory() as d,
            patch("kairos.research_shadow.load_sdk", return_value=sdk),
        ):
            with self.assertRaisesRegex(ValueError, "ChatGPT sign-in"):
                await assess([fixture()], Path(d) / "run", approve_subscription_usage=True)
            self.assertEqual(sdk.runs, [])
            self.assertEqual(sdk.closed, 1)

    async def test_failures_stop_batch_without_retry_or_fallback(self):
        for failure in ("tool", "binding", "timeout", "echo", "refs"):
            sdk = FakeSDK(fail=failure)
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as d,
                patch("kairos.research_shadow.load_sdk", return_value=sdk),
            ):
                out = Path(d) / "run"
                result = await assess(
                    [fixture(), fixture("fib-ema")],
                    out,
                    max_invocations=2,
                    approve_subscription_usage=True,
                )
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["invocations"], 1)
                self.assertEqual(len(sdk.runs), 1)
                self.assertEqual(sdk.closed, 1)
                self.assertEqual(sdk.interrupted, 1 if failure == "timeout" else 0)
                recorded = json.loads((out / "results.json").read_text())[0]
                self.assertEqual(recorded["status"], "failed")
                if failure != "timeout":
                    self.assertEqual(recorded["usage"]["inputTokens"], 20)

    async def test_cap_duplicate_and_invalid_inputs_fail_before_sdk_or_directory(self):
        cases = [([fixture(), fixture("fib-ema")], 1), ([fixture(), fixture()], 2), ([], 1)]
        with tempfile.TemporaryDirectory() as d, patch("kairos.research_shadow.load_sdk") as sdk:
            for snapshots, cap in cases:
                with self.assertRaises(ValueError):
                    await assess(
                        snapshots,
                        Path(d) / "run",
                        max_invocations=cap,
                        approve_subscription_usage=True,
                    )
            sdk.assert_not_called()
            self.assertFalse((Path(d) / "run").exists())

    def test_sdk_environment_blanks_inherited_credentials(self):
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
            self.assertEqual(
                environment(),
                {"HOME": "/tmp/home", "PATH": "/bin", "OPENAI_API_KEY": "", "CODEX_API_KEY": ""},
            )

    def test_native_echo_requires_exact_input_without_allowing_tools_or_duplicates(self):
        echo = {
            "type": "userMessage",
            "content": [{"type": "text", "text": "{}", "text_elements": []}],
        }

        def turn(items):
            return SimpleNamespace(
                status="completed",
                error=None,
                final_response="{}",
                items=[JsonModel(i) for i in items],
            )

        self.assertEqual(validate_turn(turn([echo, {"type": "agentMessage"}]), "{}")[0], echo)
        bad = copy.deepcopy(echo)
        bad["content"][0]["text"] = "different input"
        image = {"type": "userMessage", "content": [{"type": "image"}]}
        for items in (
            [bad],
            [echo, echo],
            [image],
            [echo, {"type": "commandExecution"}],
            [echo, {"type": "mcpToolCall"}],
        ):
            with self.assertRaises(ValueError):
                validate_turn(turn(items), "{}")

    def test_unpinned_sdk_is_refused_without_constructing_a_client(self):
        with patch(
            "kairos.research_shadow.importlib.import_module",
            return_value=SimpleNamespace(__version__="other"),
        ):
            with self.assertRaises(ValueError):
                load_sdk()


if __name__ == "__main__":
    unittest.main()
