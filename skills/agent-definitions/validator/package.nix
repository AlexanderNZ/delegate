# The agent-definitions renderer and validator as a Nix-built Python
# application. pyyaml is the one runtime dependency: agent frontmatter is
# YAML, and a hand-rolled parser would validate a different language from the
# one the harness reads. The rendered guards read their hook input with
# python3, so the check needs no jq. git is a check input because the verifier-brief tests build a scratch
# repository and read real diffs from it.
#
# neutralityDenylist is the path of a private denylist file. With it, the
# neutrality test scans the kit (agent-delegation/ and agent-definitions/)
# for its terms. The build copies only this directory, so the kit comes in as
# a second source. Without it, the neutrality test skips with a notice.
{
  lib,
  python3,
  git,
  neutralityDenylist ? null,
}:
let
  kit = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../agent-delegation
      ../../agent-definitions
    ];
  };
in
python3.pkgs.buildPythonApplication {
  pname = "agent-definitions";
  version = "0.1.0";
  pyproject = true;

  src = lib.cleanSource ./.;

  build-system = [ python3.pkgs.setuptools ];
  dependencies = [ python3.pkgs.pyyaml ];

  nativeCheckInputs = [
    python3.pkgs.pytestCheckHook
    git
  ];
  pythonImportsCheck = [ "agent_definitions" ];
  # -rs prints the reason of each skipped test, so a skipped neutrality check
  # shows in the build log.
  pytestFlags = [ "-rs" ];

  env = lib.optionalAttrs (neutralityDenylist != null) {
    AGENT_DEFINITIONS_DENYLIST = "${neutralityDenylist}";
    AGENT_DEFINITIONS_KIT_ROOT = "${kit}";
  };

  meta = {
    description = "Render and validate specialist-verifier agent pairs for Claude Code and OpenCode";
    mainProgram = "agent-definitions";
  };
}
