# Release process

Work lands in `develop` through reviewed PRs. A release branch is cut from `origin/develop`, records that commit in `.github/release.json`, and is merged into `main` through a reviewed PR. The merge triggers tagging, GitHub publication and a reviewed back-merge into `develop`. Publishing never deploys or restarts a trading instance.

## Versioning

Use conventional commits and `release/X.Y.Z` branch names, with `vX.Y.Z` tags:

- `feat` recommends a minor release.
- `fix`, `patch`, `perf` and `revert` recommend a patch release.
- `!` or a `BREAKING CHANGE:` / `BREAKING-CHANGE:` footer recommends a major release, including during `0.x` development.
- Documentation, tests and maintenance alone do not trigger a version recommendation.

The initial release is explicitly `0.1.0`. Later releases must meet the recommended minimum version. Higher versions permit an intentional milestone such as `1.0.0`; versions and tags cannot be reused for a different commit. Merge commits are excluded from the changelog so merged feature commits are not listed twice.

## One-time authentication

Configure these repository **Actions secrets** using dedicated, expiring fine-grained PATs restricted to `kyaulabs/kairos`. Do not copy general-purpose local login tokens, put tokens in files, or paste them into issues or chat.

| Secret | Account | Repository permissions |
| --- | --- | --- |
| `RELEASE_BOT_TOKEN` | `kyaulabs-bot` | Contents: read/write; Pull requests: read/write; Actions: read |
| `RELEASE_REVIEW_TOKEN` | `kyau` | Contents: read; Pull requests: read/write; Actions: read |

Metadata access is implicit. Both accounts must have the corresponding repository access, and organization approval may be required for the tokens. Rotate them before expiry. The workflow verifies each token's account before privileged operations. Reviewer credentials are used only after the release has passed CI and the back-merge PR's independent CI has passed.

The bot creates the tag, release and back-merge PR and performs the merge. `kyau` completes the back-merge Test Plan and approves with `✔️ Approved by: @kyau`. Normal feature/release PRs still require review; only the workflow's narrowly validated back-merge PR is automatically approved. Repository branch protections are not relaxed. Conflicts, failed checks, invalid identities or expired tokens stop the workflow rather than bypassing rules.

Repository-wide auto-merge need not be enabled: the workflow waits for checks and approval, then requests a normal protected merge. Dedicated PATs ensure bot-created PRs trigger CI, unlike events created with the default `GITHUB_TOKEN`.

## Prepare a release

First merge the intended changes and release tooling into `develop`. From a clean tracked working tree:

```sh
python3 .github/scripts/release.py prepare 0.1.0
```

This fetches `origin/develop`, creates `release/0.1.0` from that exact commit, records provenance and uses `uv version --no-sync` to update project/lock versions without modifying the runtime environment. For later releases, supply the next version. Review the diff, then stage only the intended release metadata:

```sh
git add .github/release.json pyproject.toml uv.lock
git commit -S -m 'chore(release): prepare 0.1.0' \
  -m 'Cut the release from the recorded develop commit. Align package metadata and the lockfile for the reviewed main release.'
git push -u origin release/0.1.0
```

The commit is authored/signed by `kyau`; existing hooks remain enabled. Use the approved credentials to create the branch if its creation rules require them. Have `kyaulabs-bot` open a PR from `release/0.1.0` into `main` using `.github/PULL_REQUEST_TEMPLATE.md`. Complete the Test Plan after CI succeeds, obtain `kyau` approval, and merge as `kyaulabs-bot` using a **merge commit**, not squash or rebase.

The workflow validates the branch name, develop ancestry, package and lock versions, conventional-commit version floor, merge ancestry and verified merge signature. It runs CI again against the exact merged commit before publishing. Do not merge the first release until both automation secrets are configured.

## Automatic publication and back-merge

The Release workflow creates an immutable `vX.Y.Z` tag at the verified main merge commit and publishes notes rendered from `.github/RELEASE_TEMPLATE.md`. The template uses the existing dark-background Kairos logo and tag-pinned local Python/nginx installation and update instructions. Changes are collected from the preceding version tag, or the full non-merge history for the first release. GitHub supplies the source archives; licensed fonts and local credentials are not included.

A temporary `ci/kyau-<six hex digits>-backmerge-X.Y.Z` branch points at that same verified release commit. The bot opens its PR into `develop` using the repository template. Independent PR CI must pass against the current develop base before its Test Plan is marked complete and `kyau` approval is submitted. The bot checks CI and approval again, merges normally and deletes only that temporary branch. `main`, `develop` and `release/X.Y.Z` are never deleted by the workflow.

Release workflows are serialized and never canceled for newer releases. An interrupted run can be rerun; existing matching tags, published releases and back-merge PRs are reused. Moved tags/branches, duplicate back-merge candidates and PRs closed without merging fail rather than being silently replaced.

## Recover a failed workflow

Inspect the failed job, fix the reported cause, then rerun the failed jobs. Alternatively, dispatch the workflow with the original, already-merged release PR number:

```sh
gh workflow run release.yml --ref main -f release_pr=123
```

Dispatch cannot publish an arbitrary commit, an unmerged PR or a fork release. Do not delete or move a published tag to retry. If the back-merge conflicts with newer development, resolve it through a separately signed, reviewed PR; the workflow deliberately refuses to merge a moved back-merge head. Monitor all jobs through completion, including the final back-merge, before declaring the release finished.
