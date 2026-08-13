# Releasing rtl-buddy-xeno

This package publishes to PyPI via **release-on-merge** — the same
path as [`rtl_buddy`](https://github.com/rtl-buddy/rtl_buddy). The
workflow is in [`.github/workflows/release.yml`](.github/workflows/release.yml).

Authentication uses OIDC **trusted publishing** — no API tokens, no
secrets in the repo. The version is derived from the git tag by
hatch-vcs; there is no version field to bump in `pyproject.toml`.

## Per-release flow

### Stable release

Label the PR with exactly one of `version/patch`, `version/minor`, or
`version/major` before merging to `main`. On merge, the workflow:

1. Computes the next `vMAJOR.MINOR.PATCH` tag from the latest stable
   tag and pushes it.
2. Creates a GitHub release (not marked pre-release).
3. Builds the sdist + wheel (hatch-vcs derives the version from the
   tag) and verifies metadata with twine.
4. Publishes to PyPI via trusted publishing.

PRs without a version label merge without releasing.

Version bump guidance (SemVer):

- `version/patch` — bug fixes (`0.1.0` → `0.1.1`)
- `version/minor` — new operators / new public surface
  (`0.1.0` → `0.2.0`)
- `version/major` — API-breaking changes (post-1.0 only)

### Pre-release (RC)

Cut from any branch via the Actions tab → *Release* → Run workflow,
with the **Mark as pre-release** checkbox enabled:

1. The workflow appends `rcN` to the computed base tag (PEP 440). If
   `v0.2.0rc1` already exists, the next is `v0.2.0rc2`.
2. The GitHub release is marked **pre-release**.
3. The wheel publishes to PyPI as a pre-release version (e.g.
   `0.2.0rc1`). Unqualified version ranges (`>=0.1.0`) will not
   resolve to it — this is the dry-run mechanism; there is no
   TestPyPI target.

The version is computed from the latest stable tag at dispatch time.

## One-time setup

### 1. Configure the trusted publisher on PyPI

On <https://pypi.org/manage/account/publishing/>, add a pending
publisher (PyPI calls it "pending" until the first release):

| Field        | Value             |
| ------------ | ----------------- |
| Project name | `rtl-buddy-xeno`  |
| Owner        | `rtl-buddy`       |
| Repository   | `rtl-buddy-xeno`  |
| Workflow     | `release.yml`     |
| Environment  | `pypi`            |

### 2. Create the GitHub environment

Settings → Environments → New environment → `pypi`. Restrict it to
the `main` branch and (optionally) require manual review before
deploying.

### 3. Labels

The `version/{patch,minor,major}` labels must exist on the repo
(already created; the taxonomy lives in rtl_buddy's
`.github/labels.json`).

### 4. Verify the [verible] extra's resolvability

`rtl-buddy-xeno[verible]` depends on `rtl-buddy-sch>=0.7.0` — the
viewer's PyPI distribution, which renamed from `rtl-buddy-view` at
0.7.0 ([sch#157](https://github.com/rtl-buddy/rtl-buddy-sch/issues/157)).
Releases up to 0.5.0 are on PyPI under the old name and it is frozen
there; 0.7.0 onward is the same project under the new one. Both that
extra and `[slang]`'s `pyslang` resolve from PyPI unaided, so neither
blocks a release — what to verify here is that the extra still names
the distribution the viewer currently publishes under.

Without `[verible]`, only the regex-based operators
(`CLOCK_POLARITY_SWAP`, `ATTRIBUTE_TOGGLE`) function; that path needs
nothing but xeno itself.

Consumers carrying the old distribution should `pip uninstall -y
rtl-buddy-view` before installing: pip has no rename metadata, so both
dists would otherwise be installed at once, each claiming the same
`rtl_buddy_view` import package.

## Rollback

PyPI does not allow re-uploading the same version. If a release is
broken:

1. **Yank** the broken version on PyPI (it stays installable for
   pinned consumers but disappears from default resolution) — via the
   PyPI web UI under the project's release management.
2. Fix, then cut a patch release via a `version/patch`-labeled PR.

## Conventions

- **No `CHANGELOG.md`.** Release notes live on the GitHub Releases
  page; the workflow links the full changelog diff since the previous
  tag.
- **Tag format**: `v<major>.<minor>.<patch>` (`rcN` suffix for
  pre-releases). Tags are created by the workflow — don't hand-tag.
- **Don't amend tagged commits**. If the release commit needs a fix,
  cut a patch release instead.
