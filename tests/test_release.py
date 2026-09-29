import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "release_tools", Path(__file__).resolve().parents[1] / ".github/scripts/release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_conventional_version_recommendations(self):
        cases = [
            (None, [], "0.1.0"),
            ("0.1.0", ["fix(fees): recover reads"], "0.1.1"),
            ("0.1.0", ["patch: correct rounding", "perf: reduce polling"], "0.1.1"),
            ("0.1.0", ["feat: add pullbacks", "fix: rounding"], "0.2.0"),
            ("0.1.0", ["feat!: change storage format"], "1.0.0"),
            ("1.2.3", ["refactor: change API\n\nBREAKING CHANGE: remove endpoint"], "2.0.0"),
            ("1.2.3", ["docs: clarify setup", "chore: tooling"], None),
        ]
        for previous, messages, expected in cases:
            with self.subTest(messages=messages):
                self.assertEqual(release.recommendation(previous, messages), expected)
        for invalid in (
            "v0.1.0",
            "01.2.3",
            "0.1",
            "1.2.3-rc1",
            "1.2.3\nBAD=1",
            "1.2.3;echo unsafe",
        ):
            with self.assertRaises(ValueError):
                release.version(invalid)

    def test_notes_use_dark_logo_tagged_instructions_and_real_change_range(self):
        notes = release.notes(
            {
                "version": "0.2.0",
                "previous": "0.1.0",
                "changes": [
                    ("a" * 40, "feat: new <unsafe> [label]"),
                    ("b" * 40, "fix!: changed behavior"),
                ],
            }
        )
        self.assertIn("/v0.2.0/.github/media/kairos-dark.svg", notes)
        self.assertIn("compare/v0.1.0...v0.2.0", notes)
        self.assertIn("uv run --no-sync kairos", notes)
        self.assertIn("nginx and systemd", notes)
        self.assertIn("⚠️ Breaking changes", notes)
        self.assertIn("&lt;unsafe&gt;", notes)
        self.assertNotIn("{{VERSION}}", notes)
        self.assertNotIn("```sh\nphp -S", notes)

    def test_pr_body_uses_repository_template_with_honest_pending_ci(self):
        body = release.backmerge_body("0.1.0")
        self.assertIn("## 🧪 Test Plan", body)
        self.assertIn("<!-- kairos-backmerge:v0.1.0 -->", body)
        self.assertIn("- [ ] Back-merge PR CI", body)
        self.assertNotIn("<# total>", body.replace("## 📝 Commits (<# total>)", ""))

    def test_fork_or_unmerged_release_cannot_reach_tagging(self):
        for pr in (
            {"merged": False, "base": {"ref": "main"}},
            {"merged": True, "base": {"ref": "develop"}},
            {
                "merged": True,
                "base": {"ref": "main"},
                "head": {"repo": {"full_name": "someone/fork"}},
            },
        ):
            with (
                patch.dict("os.environ", {"GITHUB_REPOSITORY": "kyaulabs/kairos"}),
                patch.object(release, "api", return_value=pr),
                patch.object(release, "git") as git,
            ):
                with self.assertRaises(ValueError):
                    release.resolve("12")
                git.assert_not_called()

    def test_provenance_versions_and_bump_are_checked_before_release(self):
        base = "a" * 40
        project = '[project]\nname="kairos-trader"\nversion="0.2.0"\n'
        lock = '[[package]]\nname="kairos-trader"\nversion="0.2.0"\n'

        def git(*args):
            if args[0] == "show":
                return {
                    "head:.github/release.json": json.dumps(
                        {"version": "0.2.0", "develop_sha": base}
                    ),
                    "head:pyproject.toml": project,
                    "head:uv.lock": lock,
                }[args[1]]
            return ""

        with (
            patch.object(release, "git", side_effect=git) as calls,
            patch.object(release, "previous_version", return_value="0.1.0"),
            patch.object(release, "records", return_value=[("b" * 40, "feat: new feature")]),
        ):
            self.assertEqual(release.validate_tree("head", "0.2.0")[0], "0.1.0")
            self.assertIn(
                unittest.mock.call("merge-base", "--is-ancestor", base, "origin/develop"),
                calls.call_args_list,
            )
            lock = lock.replace("0.2.0", "0.1.0")
            with self.assertRaisesRegex(ValueError, "Locked project"):
                release.validate_tree("head", "0.2.0")

    def test_approval_requires_successful_ci_and_completes_test_plan_first(self):
        rel = {"version": "0.1.0", "sha": "a" * 40}
        pr = {"merged": False, "body": release.backmerge_body("0.1.0")}
        with (
            patch.object(release, "identity"),
            patch.object(release, "wait_for_ci", return_value=pr),
            patch.object(release, "pages", return_value=[]),
            patch.object(release, "api") as api,
        ):
            release.approve("3", rel)
            self.assertEqual(api.call_args_list[0].kwargs["method"], "PATCH")
            self.assertNotIn("- [ ]", api.call_args_list[0].kwargs["body"])
            self.assertEqual(api.call_args_list[1].kwargs["body"], "✔️ Approved by: @kyau")
        with (
            patch.object(release, "identity"),
            patch.object(release, "wait_for_ci", side_effect=ValueError("CI failed")),
            patch.object(release, "api") as api,
        ):
            with self.assertRaises(ValueError):
                release.approve("3", rel)
            api.assert_not_called()

    def test_prepare_never_switches_a_dirty_tracked_worktree(self):
        with patch.object(release, "git", return_value=" M kairos/engine.py") as git:
            with self.assertRaisesRegex(ValueError, "Commit tracked"):
                release.prepare("0.1.0")
            git.assert_called_once_with("status", "--porcelain", "--untracked-files=no")

    def test_publication_retry_reuses_tag_release_and_backmerge(self):
        rel = {"version": "0.1.0", "sha": "a" * 40, "previous": None, "changes": []}
        state = {"tag": False, "release": False, "pr": None, "refs": []}

        def git(*args):
            return "v0.1.0" if state["tag"] else ""

        def run(*args):
            if args[:3] == ("gh", "release", "create"):
                state["release"] = True
            return "123abc"

        def pages(path):
            if path == "releases":
                return (
                    [{"tag_name": "v0.1.0", "draft": False, "prerelease": False}]
                    if state["release"]
                    else []
                )
            return [state["pr"]] if state["pr"] else []

        def api(path, **fields):
            if path == "git/refs":
                state["refs"].append(fields["ref"])
                if fields["ref"].startswith("refs/tags/"):
                    state["tag"] = True
            elif path.startswith("git/matching-refs"):
                return []
            elif path == "pulls":
                state["pr"] = {
                    "number": 42,
                    "body": fields["body"],
                    "user": {"login": "kyaulabs-bot"},
                }
                return state["pr"]

        with (
            patch.object(release, "identity") as identity,
            patch.object(release, "git", side_effect=git),
            patch.object(release, "run", side_effect=run),
            patch.object(release, "pages", side_effect=pages),
            patch.object(release, "api", side_effect=api),
        ):
            self.assertEqual(release.publish(rel), 42)
            self.assertEqual(release.publish(rel), 42)
            self.assertEqual(
                state["refs"], ["refs/tags/v0.1.0", "refs/heads/ci/kyau-123abc-backmerge-0.1.0"]
            )
            identity.assert_called_with("kyaulabs-bot")

    def test_backmerge_guard_rejects_protected_heads_and_moved_commits(self):
        rel = {"version": "0.1.0", "sha": "a" * 40}
        pr = {
            "user": {"login": "kyaulabs-bot"},
            "base": {"ref": "develop"},
            "head": {"repo": {"full_name": "kyaulabs/kairos"}, "ref": "main", "sha": "a" * 40},
            "body": "<!-- kairos-backmerge:v0.1.0 -->",
            "merged": False,
            "state": "open",
        }
        with (
            patch.dict("os.environ", {"GITHUB_REPOSITORY": "kyaulabs/kairos"}),
            patch.object(release, "api", return_value=pr),
        ):
            with self.assertRaisesRegex(ValueError, "branch"):
                release.checked_backmerge(42, rel)
            pr["head"]["ref"] = "ci/kyau-123abc-backmerge-0.1.0"
            pr["head"]["sha"] = "b" * 40
            with self.assertRaisesRegex(ValueError, "moved"):
                release.checked_backmerge(42, rel)

    def test_latest_failed_backmerge_ci_cannot_use_an_older_success(self):
        rel = {"version": "0.1.0", "sha": "a" * 40}
        pr = {"merged": False, "base": {"sha": "b" * 40}}
        common = {
            "run_attempt": 1,
            "status": "completed",
            "html_url": "https://example.invalid/run",
            "pull_requests": [{"number": 42, "base": pr["base"]}],
        }
        runs = [
            {**common, "id": 1, "conclusion": "success"},
            {**common, "id": 2, "conclusion": "failure"},
        ]
        with (
            patch.object(release, "checked_backmerge", return_value=pr),
            patch.object(release, "api", return_value={"workflow_runs": runs}),
        ):
            with self.assertRaisesRegex(ValueError, "CI failed"):
                release.wait_for_ci(42, rel)

    def test_tag_retargeting_is_rejected(self):
        sha, head, base = "a" * 40, "b" * 40, "c" * 40
        pr = {
            "merged": True,
            "base": {"ref": "main"},
            "head": {"repo": {"full_name": "kyaulabs/kairos"}, "ref": "release/0.1.0", "sha": head},
            "merge_commit_sha": sha,
        }

        def git(*args):
            if args[0] == "rev-list":
                return f"{sha} {base} {head}"
            if args[0] == "show":
                return '[project]\nversion="0.1.0"'
            if args[0] == "tag":
                return "v0.1.0"
            if args[0] == "rev-parse":
                return "d" * 40
            return ""

        with (
            patch.dict("os.environ", {"GITHUB_REPOSITORY": "kyaulabs/kairos"}),
            patch.object(
                release, "api", side_effect=[pr, {"commit": {"verification": {"verified": True}}}]
            ),
            patch.object(release, "git", side_effect=git),
            patch.object(release, "validate_tree", return_value=(None, [])),
        ):
            with self.assertRaisesRegex(ValueError, "points elsewhere"):
                release.resolve("1")
