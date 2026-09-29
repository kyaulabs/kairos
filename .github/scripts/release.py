#!/usr/bin/env python3
"""Release orchestration using git, gh and the Python standard library.

Publishing accepts only reviewed, merged, same-repository release PRs. No force
pushes, unsigned local commits, protection bypasses or deployment operations.
"""

import argparse
import html
import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
COMMIT = re.compile(r"(?P<type>\w+)(?:\([^\n)]+\))?(?P<breaking>!)?: (?P<title>.+)")
ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def git(*args):
    return run("git", "-c", "core.fileMode=false", *args)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version(value):
    require(
        isinstance(value, str) and VERSION.fullmatch(value), f"Invalid release version: {value!r}"
    )
    return tuple(map(int, value.split(".")))


def recommendation(previous, messages):
    if previous is None:
        return "0.1.0"
    major, minor, patch = version(previous)
    bump = 0
    for message in messages:
        match = COMMIT.fullmatch(message.splitlines()[0])
        if (match and match["breaking"]) or re.search(r"(?m)^BREAKING[ -]CHANGE:", message):
            bump = max(bump, 3)
        elif match and match["type"] == "feat":
            bump = max(bump, 2)
        elif match and match["type"] in {"fix", "perf", "patch", "revert"}:
            bump = max(bump, 1)
    return (
        f"{major + 1}.0.0"
        if bump == 3
        else f"{major}.{minor + 1}.0"
        if bump == 2
        else f"{major}.{minor}.{patch + 1}"
        if bump == 1
        else None
    )


def api(path, *, method="GET", **fields):
    repo = os.environ.get("GITHUB_REPOSITORY", "kyaulabs/kairos")
    args = ["gh", "api", "--method", method, f"repos/{repo}/{path}"]
    for key, value in fields.items():
        args.extend(("-f", f"{key}={value}"))
    return json.loads(run(*args) or "null")


def pages(path):
    result = []
    for page in range(1, 101):
        batch = api(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ValueError("Pagination limit exceeded; refusing incomplete release history")


def identity(expected):
    require(bool(os.environ.get("GH_TOKEN")), "Required dedicated Actions token is missing")
    require(
        json.loads(run("gh", "api", "user"))["login"] == expected,
        f"Token must belong to {expected}",
    )


def records(previous, sha):
    span = f"v{previous}..{sha}" if previous else sha
    raw = git("log", "--no-merges", "--format=%H%x00%B%x00", span)
    parts = raw.split("\0")
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(0, len(parts) - 1, 2)]


def previous_version(target):
    tags = [t[1:] for t in git("tag", "--list", "v*").splitlines() if VERSION.fullmatch(t[1:])]
    older = [t for t in tags if version(t) < version(target)]
    return max(older, key=version) if older else None


def validate_tree(head, target):
    version(target)
    provenance = json.loads(git("show", f"{head}:.github/release.json"))
    base = provenance.get("develop_sha", "")
    require(
        provenance.get("version") == target and re.fullmatch(r"[0-9a-f]{40}", base),
        "Missing/invalid release provenance",
    )
    git("merge-base", "--is-ancestor", base, "origin/develop")
    git("merge-base", "--is-ancestor", base, head)
    project = tomllib.loads(git("show", f"{head}:pyproject.toml"))["project"]
    require(project["version"] == target, "Branch and project versions differ")
    lock = tomllib.loads(git("show", f"{head}:uv.lock"))
    package = next(p for p in lock["package"] if p["name"] == project["name"])
    require(package["version"] == target, "Locked project version differs")
    previous = previous_version(target)
    if previous:
        git("merge-base", "--is-ancestor", f"v{previous}", head)
    changes = records(previous, head)
    expected = recommendation(previous, [message for _, message in changes])
    require(
        expected is not None and version(target) >= version(expected),
        f"Conventional commits require at least {expected or 'a releasable change'}",
    )
    require(previous is not None or target == "0.1.0", "The first release must be 0.1.0")
    return previous, changes


def resolve(number):
    pr = api(f"pulls/{int(number)}")
    repo = os.environ["GITHUB_REPOSITORY"]
    require(pr["merged"] and pr["base"]["ref"] == "main", "Not a merged release into main")
    require(pr["head"]["repo"]["full_name"] == repo, "Fork releases are prohibited")
    require(pr["head"]["ref"].startswith("release/"), "Not a release branch")
    target = pr["head"]["ref"].removeprefix("release/")
    version(target)
    sha = pr["merge_commit_sha"]
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Invalid merge SHA")
    git("fetch", "origin", "main", "develop", "--tags")
    git("merge-base", "--is-ancestor", sha, "origin/main")
    parents = git("rev-list", "--parents", "-n", "1", sha).split()
    require(
        len(parents) == 3 and parents[2] == pr["head"]["sha"], "Release must use a merge commit"
    )
    require(
        api(f"commits/{sha}")["commit"]["verification"]["verified"],
        "Release merge commit is not verified",
    )
    previous, changes = validate_tree(pr["head"]["sha"], target)
    require(
        tomllib.loads(git("show", f"{sha}:pyproject.toml"))["project"]["version"] == target,
        "Merged project version differs",
    )
    # Existing immutable tags are allowed only for an idempotent rerun.
    tags = git("tag", "--list", f"v{target}")
    if tags:
        require(
            git("rev-parse", f"v{target}^{{commit}}") == sha, "Release tag already points elsewhere"
        )
    return {"version": target, "sha": sha, "previous": previous, "changes": changes}


def notes(release):
    repo = os.environ.get("GITHUB_REPOSITORY", "kyaulabs/kairos")
    groups = {}
    for sha, message in release["changes"]:
        match = COMMIT.fullmatch(message.splitlines()[0])
        kind = match["type"] if match else "other"
        heading = (
            "⚠️ Breaking changes"
            if (match and match["breaking"]) or re.search(r"(?m)^BREAKING[ -]CHANGE:", message)
            else {
                "feat": "✨ Features",
                "fix": "🐛 Fixes",
                "patch": "🐛 Fixes",
                "perf": "⚡ Performance",
                "docs": "📚 Documentation",
            }.get(kind, "🛠️ Maintenance")
        )
        title = html.escape(message.splitlines()[0]).replace("[", r"\[").replace("]", r"\]")
        groups.setdefault(heading, []).append(
            f"- {title} ([{sha[:7]}](https://github.com/{repo}/commit/{sha}))"
        )
    previous = release["previous"]
    tag = "v" + release["version"]
    values = {
        "REPOSITORY": repo,
        "VERSION": release["version"],
        "TAG": tag,
        "PREVIOUS": "v" + previous if previous else "the beginning of the project",
        "CHANGES": "\n\n".join(
            "### " + heading + "\n\n" + "\n".join(rows) for heading, rows in groups.items()
        ),
        "COMPARE": f"[Full comparison](https://github.com/{repo}/compare/v{previous}...{tag})"
        if previous
        else f"[Initial release history](https://github.com/{repo}/commits/{tag})",
        "VERIFICATION": "Release CI passed Python lint/format checks, dashboard syntax/component tests and backend regression tests on Python 3.12, 3.13 and 3.14. No exchange credentials or live orders are used by CI.",
    }
    text = (ROOT / ".github/RELEASE_TEMPLATE.md").read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def backmerge_body(target):
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    sections = {
        "## 📋 Summary": f"Synchronize released `v{target}` from main into develop.\n\n<!-- kairos-backmerge:v{target} -->",
        "## 📦 Changes by Phase": "- Preserve the release merge ancestry, version and reviewed release changes.",
        "## 📜 ADRs": "None.",
        "## ✅ Verification": "Release CI passed. The back-merge PR must independently pass CI before approval or merge.",
        "## 🏗️ Architect Conditions (if applicable)": "Not applicable.",
        "## 📝 Commits (<# total>)": "No new authored commits; this branch points at the verified release merge commit.",
        "## 🧪 Test Plan": "- [x] Release CI passed on Python 3.12–3.14 and Node.js 22.\n- [x] Release version, develop provenance and immutable tag verified.\n- [ ] Back-merge PR CI passed against develop.",
    }
    template = re.sub(r"<!--.*?-->", "", template, flags=re.S).strip()
    for heading, body in sections.items():
        require(heading in template, f"PR template section missing: {heading}")
        template = template.replace(heading, heading + "\n\n" + body)
    return template.strip().replace("## 📝 Commits (<# total>)", "## 📝 Commits") + "\n"


def publish(release):
    identity("kyaulabs-bot")
    target, sha = release["version"], release["sha"]
    tag = "v" + target
    if not git("tag", "--list", tag):
        api("git/refs", method="POST", ref="refs/tags/" + tag, sha=sha)
    existing = [r for r in pages("releases") if r["tag_name"] == tag]
    if existing:
        require(
            not existing[0]["draft"] and not existing[0]["prerelease"],
            "Existing release is not a stable published release",
        )
    else:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as body:
            body.write(notes(release))
            body.flush()
            run(
                "gh",
                "release",
                "create",
                tag,
                "--verify-tag",
                "--title",
                "Kairos " + tag,
                "--notes-file",
                body.name,
            )
    marker = f"<!-- kairos-backmerge:{tag} -->"
    matches = [
        p
        for p in pages("pulls?state=all&base=develop")
        if marker in (p["body"] or "") and p["user"]["login"] == "kyaulabs-bot"
    ]
    require(len(matches) <= 1, "Ambiguous back-merge PRs")
    if matches:
        return matches[0]["number"]
    suffix = "-backmerge-" + target
    candidates = [
        r
        for r in api("git/matching-refs/heads/ci/kyau-")
        if r["ref"].endswith(suffix) and r["object"]["sha"] == sha
    ]
    require(len(candidates) <= 1, "Ambiguous back-merge branches")
    branch = (
        candidates[0]["ref"].removeprefix("refs/heads/")
        if candidates
        else "ci/kyau-" + run("openssl", "rand", "-hex", "3") + suffix
    )
    if not candidates:
        api("git/refs", method="POST", ref="refs/heads/" + branch, sha=sha)
    pr = api(
        "pulls",
        method="POST",
        title=f"chore(release): back-merge {tag} into develop",
        head=branch,
        base="develop",
        body=backmerge_body(target),
    )
    return pr["number"]


def checked_backmerge(number, release):
    pr = api(f"pulls/{int(number)}")
    require(
        pr["user"]["login"] == "kyaulabs-bot" and pr["base"]["ref"] == "develop",
        "Unexpected back-merge identity/base",
    )
    require(
        pr["head"]["repo"]["full_name"] == os.environ["GITHUB_REPOSITORY"],
        "Fork back-merges prohibited",
    )
    require(
        re.fullmatch(
            r"ci/kyau-[0-9a-f]{6}-backmerge-" + re.escape(release["version"]), pr["head"]["ref"]
        ),
        "Unexpected back-merge branch",
    )
    require(pr["head"]["sha"] == release["sha"], "Back-merge branch moved")
    require(
        f"<!-- kairos-backmerge:v{release['version']} -->" in (pr["body"] or ""),
        "Missing back-merge provenance",
    )
    require(pr["merged"] or pr["state"] == "open", "Back-merge was closed without merging")
    return pr


def wait_for_ci(number, release):
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        pr = checked_backmerge(number, release)
        if pr["merged"]:
            return pr
        runs = api(
            f"actions/workflows/ci.yml/runs?event=pull_request&head_sha={release['sha']}&per_page=100"
        )["workflow_runs"]
        runs = [
            r
            for r in runs
            if any(
                p["number"] == int(number) and p["base"]["sha"] == pr["base"]["sha"]
                for p in r["pull_requests"]
            )
        ]
        if runs:
            latest = max(runs, key=lambda r: (r["id"], r["run_attempt"]))
            if latest["status"] == "completed":
                require(
                    latest["conclusion"] == "success", f"Back-merge CI failed: {latest['html_url']}"
                )
                return pr
        time.sleep(15)
    raise ValueError("Timed out waiting for back-merge CI; no approval or merge performed")


def approve(number, release):
    identity("kyau")
    pr = wait_for_ci(number, release)
    if pr["merged"]:
        return
    body = pr["body"].replace(
        "- [ ] Back-merge PR CI passed against develop.",
        "- [x] Back-merge PR CI passed against develop.",
    )
    require("- [ ]" not in body, "Incomplete Test Plan")
    api(f"pulls/{int(number)}", method="PATCH", body=body)
    reviews = pages(f"pulls/{int(number)}/reviews")
    if not any(
        r["user"]["login"] == "kyau"
        and r["state"] == "APPROVED"
        and r["commit_id"] == release["sha"]
        for r in reviews
    ):
        api(
            f"pulls/{int(number)}/reviews",
            method="POST",
            event="APPROVE",
            commit_id=release["sha"],
            body="✔️ Approved by: @kyau",
        )


def merge(number, release):
    identity("kyaulabs-bot")
    pr = wait_for_ci(number, release)
    if not pr["merged"]:
        require("- [ ]" not in pr["body"], "Incomplete Test Plan")
        reviews = pages(f"pulls/{int(number)}/reviews")
        require(
            any(
                r["user"]["login"] == "kyau"
                and r["state"] == "APPROVED"
                and r["commit_id"] == release["sha"]
                for r in reviews
            ),
            "Missing kyau approval",
        )
        api(
            f"pulls/{int(number)}/merge",
            method="PUT",
            merge_method="merge",
            sha=release["sha"],
            commit_title=f"chore(release): back-merge v{release['version']} into develop",
            commit_message="Synchronize the verified release ancestry and version after successful CI and kyau approval. Preserve branch protections and signed GitHub merge commits.",
        )
    require(checked_backmerge(number, release)["merged"], "Back-merge did not complete")
    # Never delete main, develop or release/X.Y.Z.
    if api("git/matching-refs/heads/" + pr["head"]["ref"]):
        api("git/refs/heads/" + pr["head"]["ref"], method="DELETE")


def prepare(target):
    version(target)
    require(
        not git("status", "--porcelain", "--untracked-files=no"),
        "Commit tracked changes before preparing a release",
    )
    git("fetch", "origin", "develop", "main", "--tags")
    base = git("rev-parse", "origin/develop")
    prior = previous_version(target)
    expected = recommendation(prior, [message for _, message in records(prior, base)])
    require(
        expected and version(target) >= version(expected),
        f"Recommended minimum version: {expected}",
    )
    require(prior is not None or target == "0.1.0", "First release must be 0.1.0")
    git("switch", "-c", "release/" + target, "origin/develop")
    (ROOT / ".github/release.json").write_text(
        json.dumps({"version": target, "develop_sha": base}, indent=2) + "\n"
    )
    run("uv", "version", "--no-sync", target)
    print(
        "Prepared release/"
        + target
        + "; sign and commit .github/release.json, pyproject.toml and uv.lock, then open a bot-authored PR to main."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("prepare", "validate", "publish", "approve", "merge", "check-pr")
    )
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.action == "prepare":
        return prepare(args.value)
    if args.action == "check-pr":
        head = os.environ.get("GITHUB_HEAD_REF", "")
        if os.environ.get("GITHUB_BASE_REF") != "main":
            return
        require(head.startswith("release/"), "Only release/X.Y.Z PRs may target main")
        git("fetch", "origin", "develop", "--tags")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        pr = event["pull_request"]
        require(
            pr["head"]["repo"]["full_name"] == os.environ["GITHUB_REPOSITORY"],
            "Fork releases prohibited",
        )
        validate_tree(pr["head"]["sha"], head.removeprefix("release/"))
        return
    release = resolve(os.environ["RELEASE_PR"])
    outputs = {"version": release["version"], "sha": release["sha"]}
    if args.action == "publish":
        outputs["backmerge"] = publish(release)
    elif args.action == "approve":
        approve(os.environ["BACKMERGE_PR"], release)
    elif args.action == "merge":
        merge(os.environ["BACKMERGE_PR"], release)
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as out:
            for key, value in outputs.items():
                out.write(f"{key}={value}\n")
    print(json.dumps(outputs))


if __name__ == "__main__":
    main()
