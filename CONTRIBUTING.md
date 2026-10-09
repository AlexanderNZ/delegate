# Contributing to delegate

This page tells you how to set up the repository, how to write a change, and how to send it. You need no Nix and no help from the maintainer.

## Set up

You need Python 3.11 or later and git. The tests of the guards run the rendered hooks, and the hooks read their input with `python3`. Nix is optional. The repository has a flake for people who use Nix, and you can ignore it. If you use Nix with direnv, run `direnv allow` once, and the development shell loads in this directory.

Run these commands from the root of the repository, in a clean Python environment:

```bash
pip install . pytest
python -m pytest -rs
```

These are the commands that CI runs, so a green run on your machine is a green run in CI. A test checks that this page and the CI file agree.

One test checks the tree against a private list of terms. The list is not in the repository. Without it, that test skips with a notice, and the rest of the suite runs. You do not need the list.

## Write the test first

Write the failing test before the code that makes it pass. Run the test, and read the failure. The test must fail for the reason that you want to fix. Then write the least code that makes it pass.

A bug fix starts the same way. Write a test that shows the bug, watch it fail, and then fix the bug.

A test drives a public entry point, such as `main(argv)` or the adapter interface. It checks what a user can see: an exit code, a standard output, a file, or the state of git. A test never imports a private function and never checks a log line. A test that needs git uses a real temporary git repository. A mock stands only at a system boundary, such as the harness process.

## Regenerate the docs

Some sections of the reference pages are generated from the code. Do not edit the text between the `generated:begin` and `generated:end` markers. After you change a flag, an exit code, a guard command or a finding code, run this command from the root of the repository. Then commit the pages that it changed:

```bash
delegate docs
```

A test fails when a committed section differs from the code. See [the reference for `delegate docs`](docs/reference/docs.md).

## Build and preview the docs

The pages in `docs/` also make a static site with [Zensical](https://zensical.org). The site has navigation, search and a tooltip for each term of the glossary. The site is not part of the package, and you do not need it to send a change.

You need [uv](https://docs.astral.sh/uv/). The file `.zensical-version` holds the one version of Zensical that the site uses. Run these commands from the root of the repository:

```bash
uvx "zensical==$(cat .zensical-version)" build --strict
uvx "zensical==$(cat .zensical-version)" serve
```

The first command builds the site into `site/`, and it fails on a dead link or a dead anchor. The second command builds the site and shows it on `localhost:8000` while you edit a page.

- To change the version, change `.zensical-version`. The version is in no other file.
- A link from a page in `docs/` to a file outside `docs/` must be a full URL, because the build cannot follow it.
- A new page in `docs/` needs a line in the `nav` of `zensical.toml`. A test fails when a page is missing there.
- The tooltips come from `docs/glossary.md`. `delegate docs` writes them to `includes/abbreviations.md`, and a test fails when that file is stale.
- To name the file that a code block holds, write the name as a title after the language: `toml title="workflow.toml"`. A bare word after the language does not render on the site, and a test fails on it.
- A diagram is a Mermaid block (```` ```mermaid ````) or an SVG with a text alternative. For an SVG, follow the `tufte-data-viz` rules, and give a light and a dark version. Never draw a diagram with box-drawing characters or arrows in a code block: it renders as code, scrolls sideways, and the theme cannot style it. A test fails on it.
- The theme is in `overrides/` (templates), `docs/stylesheets/delegate.css` and `docs/javascripts/delegate.js`. Its spec and prototype are in `design/`. Change the theme there, not in the pages.
- A reference page carries its man-page data in its front matter: `man`, `man_name` and `synopsis`. The theme shows them above the page text. A test fails when the synopsis names an option that the command does not have.
- A page whose headings start with a number sets `heading_numbers: false` in its front matter, so the theme does not number them again.

## Date every harness fact

A harness changes from one release to the next. So each fact about a harness carries the version of the harness and the date that you measured it. A fact is a command, an option, an event of a stream, or an end state.

- A reference page states the version and the date next to the facts. See [the `claude-code` adapter reference](docs/reference/claude-code-adapter.md) for an example.
- Each set of recorded streams has a `manifest.json` in `tests/fixtures/`. The manifest holds the `harness_version` and the `recorded` date of the streams. A test fails when a manifest has no version or no date.
- Write the date as `YYYY-MM-DD`. Do not write a fact that you did not measure. When you cannot measure it, say so on the page.

## Add a harness adapter

Read [how to add a harness adapter](docs/how-to/add-a-harness-adapter.md) first. It shows the interface and an example. Then tick each box before you send the adapter:

- [ ] Measure the headless command of the harness on a real machine. Record one stream for each end state, and a `manifest.json` with the version of the harness and the date.
- [ ] Write the adapter module in `src/delegate/`, beside `claude_code.py` and `opencode.py`.
- [ ] Write the contract test in `tests/`. It replays each recorded stream through a stand-in command. It checks the fields of the result, the model and the agent on the command, and the error for a harness that writes no event.
- [ ] Connect the adapter. Add a branch for its name in `get` in `src/delegate/adapters.py`. Add its name to `ADAPTERS` in `src/delegate/workflow.py`. Add a column to each tier in `src/delegate/tiers.toml`.
- [ ] Write a test that runs `delegate run` with the stand-in. It must show that the model on the command is the model of the tier column of the adapter.
- [ ] Do the live smoke run: run the adapter once against the real harness on a real machine. Write the version of the harness and the date of the run in the reference page.
- [ ] Write the reference page of the adapter in `docs/reference/`. Link it from `README.md`. Run `delegate docs`.

## Send a change

- Keep each change to one purpose. Put the test and the code that it covers in the same change.
- Run the whole suite, as the [setup](#set-up) shows. Run `delegate docs --check` too.
- List the change in `CHANGELOG.md`, under the `Unreleased` heading, in one line.
- Write the commit subject as a statement of what the change does.

## Cut a release

A maintainer cuts a release from GitHub. No local command is needed.

1. Open the **Actions** tab of the repository, and select the **Release** workflow.
2. Select **Run workflow**, and keep the `main` branch. The workflow runs on `main` only.
3. Choose the `bump` input. Choose `patch` for a fix, `minor` for a new feature, and `major` for a change that breaks a user.
4. Start the run.

The workflow runs the test suite first. When a test fails, the workflow makes no commit, no tag and no release. Each step after the suite is a command of `scripts/release.py`:

- `next-version` raises the newest tag of the form `vX.Y.Z` by the bump. The first release keeps the version in `pyproject.toml`.
- `apply` sets the version in `pyproject.toml`. It also moves the text under `Unreleased` in `CHANGELOG.md` into a dated entry. When that text is empty, the entry holds the release notes. The `Unreleased` heading stays.
- `notes` writes the release notes from the commits since the previous tag. It leaves out merge commits. It groups the rest by the prefix of the subject.

When `apply` changes a file, the workflow commits `release: vX.Y.Z`. Then it pushes, tags the commit `vX.Y.Z`, and creates the GitHub release with the notes.

To see the next version before you start the run, run this command from the root of the repository:

```bash
python scripts/release.py next-version --bump minor
```

Write each commit subject with a prefix such as `feat:` or `fix:`. A commit without a known prefix goes under "Other changes" in the notes.
