# The agent-definitions renderer and validator as a Nix-built Python
# application. pyyaml is the one runtime dependency: agent frontmatter is
# YAML, and a hand-rolled parser would validate a different language from the
# one the harness reads. The rendered guards read their hook input with
# python3, so the check needs no jq. git is a check input because the verifier-brief tests build a scratch
# repository and read real diffs from it.
#
# neutralityDenylist is the path of a private denylist file. With it, the
# neutrality test scans the kit (skills/agent-delegation/,
# skills/agent-definitions/, plugins/ and .claude-plugin/) for its terms. The
# build copies only the package, its tests and its example (not the skills or
# the docs), so the kit comes in as a second source. The second source keeps
# the layout of the repository, because the check finds the plugin
# directories in the parent of the kit root.
# Without it, the neutrality test skips with a notice.
#
# runTests decides whether the build runs the pytest suite. It is false by
# default: CI and the release job run the suite before each tag, so a consumer
# that builds or installs the package runs no test. The flake check `tests`
# sets it, and runs the suite in parallel with pytest-xdist (-n auto). A
# consumer that wants the suite with its own denylist builds
# `agent-definitions.override { neutralityDenylist = <path>; runTests = true; }`.
{
  lib,
  python3,
  git,
  neutralityDenylist ? null,
  runTests ? false,
}:
let
  kit = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./skills/agent-delegation
      ./skills/agent-definitions
      ./plugins
      ./.claude-plugin
    ];
  };

  # The package source: the files that the package and its tests read. The
  # docs, the skills and the README stay out, so the tests that read them skip
  # with a notice, as they did when this build copied only the package directory.
  source = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./pyproject.toml
      ./src
      ./tests
      ./examples/java-spring.toml
    ];
  };
in
python3.pkgs.buildPythonApplication {
  pname = "agent-definitions";
  # One source of the version: pyproject.toml. `scripts/release.py apply` edits only that file.
  version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
  pyproject = true;

  src = source;

  build-system = [ python3.pkgs.setuptools ];
  dependencies = [ python3.pkgs.pyyaml ];

  # The Python builder runs pytestCheckHook in the install check, and doCheck sets it.
  doCheck = runTests;
  nativeCheckInputs = [
    python3.pkgs.pytestCheckHook
    python3.pkgs.pytest-xdist
    git
  ];
  pythonImportsCheck = [ "delegate" ];
  # -rs prints the reason of each skipped test, so a skipped neutrality check
  # shows in the build log. -n auto runs the suite in parallel.
  pytestFlags = [
    "-rs"
    "-n"
    "auto"
  ];

  env = lib.optionalAttrs (neutralityDenylist != null) {
    AGENT_DEFINITIONS_DENYLIST = "${neutralityDenylist}";
    AGENT_DEFINITIONS_KIT_ROOT = "${kit}/skills";
  };

  meta = {
    description = "Render and validate specialist-verifier agent pairs for Claude Code and OpenCode";
    mainProgram = "agent-definitions";
  };
}
