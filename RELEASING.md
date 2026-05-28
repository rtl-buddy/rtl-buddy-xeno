# Releasing rtl-buddy-xeno

This package publishes to PyPI via OIDC **trusted publishing** —
no API tokens, no secrets in the repo. The release workflow is in
[`.github/workflows/release.yml`](.github/workflows/release.yml).

## One-time setup

### 1. Reserve the project name on PyPI

Both PyPI and TestPyPI:

- https://pypi.org/project/rtl-buddy-xeno/
- https://test.pypi.org/project/rtl-buddy-xeno/

If the name is taken, pick a fallback before anything else — every
step below assumes the name is yours.

### 2. Configure trusted publishers

On each of PyPI and TestPyPI, in the project's *Publishing* settings,
add a pending publisher (PyPI calls this "pending" until the first
release):

| Field        | Value                                                |
| ------------ | ---------------------------------------------------- |
| Owner        | `rtl-buddy`                                           |
| Repository   | `rtl-buddy-xeno`                                      |
| Workflow     | `release.yml`                                         |
| Environment  | `pypi` (for PyPI) / `testpypi` (for TestPyPI)         |

The environments must exist on the GitHub side too — Settings →
Environments → New environment → `pypi` and `testpypi`. Restrict
each to the `main` branch and (optionally) require manual review
before deploying.

### 3. Verify the [verible] extra's resolvability

`rtl-buddy-xeno[verible]` depends on `rtl-buddy-view>=0.2.0,<0.3.0`.
**The [verible] extra is unresolvable from PyPI until rtl-buddy-view
is also published.** Two consumer scenarios:

- Without `[verible]`: works as soon as xeno is on PyPI. Only the
  regex-based operators (`CLOCK_POLARITY_SWAP`, `ATTRIBUTE_TOGGLE`)
  function.
- With `[verible]`: blocked until view is on PyPI. Drop the
  `[tool.uv.sources]` block from `pyproject.toml` in the same change
  that lands view's PyPI debut, so PyPI consumers resolve cleanly
  via the version constraint.

The `[slang]` extra already resolves cleanly (pyslang is on PyPI).

## Per-release flow

1. **Pick a version**. SemVer per
   [`docs.python.org/3/distutils/configuringthebuild.html`](https://packaging.python.org/en/latest/specifications/version-specifiers/).
   - Patch bump for bug fixes (`0.1.0` → `0.1.1`)
   - Minor bump for new operators / new public surface
     (`0.1.0` → `0.2.0`)
   - Major bump for API-breaking changes (post-1.0 only)

2. **Bump the version** in `pyproject.toml`. Re-lock:
   ```bash
   uv lock
   ```

3. **Local sanity** before tagging:
   ```bash
   uv run ruff check
   uv run ruff format --check
   uv run mypy
   uv run pytest -q
   uv build              # produces dist/*.tar.gz + dist/*.whl
   uv run --with twine twine check dist/*
   ```

4. **Commit the version bump** and merge to `main` via PR.

5. **TestPyPI dry-run** (recommended for first release of each year /
   any change to the publish workflow):

   Actions tab → *Publish to PyPI* → Run workflow → target=`testpypi`.
   Verify the result on https://test.pypi.org/project/rtl-buddy-xeno/.
   Install in a clean venv to smoke-test:

   ```bash
   python -m venv /tmp/xeno-test && source /tmp/xeno-test/bin/activate
   pip install --index-url https://test.pypi.org/simple/ \
               --extra-index-url https://pypi.org/simple/ \
               rtl-buddy-xeno==<version>
   python -c "from rtl_buddy_xeno import Mutator, MutationKind; print('ok')"
   ```

6. **Tag and release**:
   ```bash
   git tag v<version>
   git push origin v<version>
   gh release create v<version> --generate-notes
   ```

   The `release: published` event fires the workflow, which builds
   and publishes to real PyPI. Watch the run; the Releases page on
   PyPI updates within a minute.

## Rollback

PyPI does not allow re-uploading the same version. If a release is
broken:

1. **Yank** the broken version on PyPI (it stays installable for
   pinned consumers but disappears from default resolution):
   ```bash
   # via the PyPI web UI, or via twine:
   twine yank rtl-buddy-xeno <version> --reason "broken release; use <next-version>"
   ```

2. Bump the patch version, fix, re-release.

## Conventions

- **No `CHANGELOG.md`.** Release notes live on the GitHub Releases
  page. `--generate-notes` populates them from merged PRs since the
  previous tag.
- **Tag format**: `v<major>.<minor>.<patch>`. The leading `v` matches
  the convention rtl-buddy-view uses.
- **Don't amend tagged commits**. If the release commit needs a fix,
  cut a patch release instead.
