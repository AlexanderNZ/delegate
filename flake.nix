{
  description = "delegate: a harness-neutral delegation kit";

  # Nix is optional. The kit needs Python 3.11 or later with pyyaml, and git.
  # This flake only packages the kit, runs its checks, and exports the skills.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "aarch64-darwin"
        "x86_64-darwin"
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (
        pkgs:
        let
          agent-definitions = pkgs.callPackage ./package.nix { };
        in
        {
          inherit agent-definitions;
          default = agent-definitions;
        }
      );

      # The package builds with no tests (see runTests in package.nix). The check
      # `tests` builds it with the full pytest suite, in parallel, so
      # `nix flake check` and CI run the suite. With no denylist, the neutrality
      # test skips with a notice.
      checks = forAllSystems (
        pkgs:
        let
          agent-definitions = self.packages.${pkgs.stdenv.hostPlatform.system}.agent-definitions;
        in
        {
          inherit agent-definitions;
          tests = agent-definitions.override { runTests = true; };
        }
      );

      # The shell runs the working copy, so an edit is live with no rebuild.
      # The three commands are thin wrappers. The shellHook puts the src
      # directory of the working copy on PYTHONPATH.
      devShells = forAllSystems (
        pkgs:
        let
          # pytest-xdist runs the suite in parallel: `pytest -n auto`.
          python = pkgs.python313.withPackages (ps: [
            ps.pytest
            ps.pytest-xdist
            ps.pyyaml
          ]);
          wrapper =
            name: module:
            pkgs.writeShellScriptBin name ''
              exec ${python}/bin/python -m ${module} "$@"
            '';
        in
        {
          default = pkgs.mkShell {
            packages = [
              python
              pkgs.git
              pkgs.bash
              pkgs.uv
              (wrapper "delegate" "delegate.cli.delegate")
              (wrapper "agent-definitions" "delegate.cli.agent_definitions")
              (wrapper "verifier-brief" "delegate.cli.brief")
            ];
            # The root comes from git, so the shell works in a subdirectory.
            shellHook = ''
              delegate_root=$(git rev-parse --show-toplevel 2>/dev/null)
              if [ -d "$delegate_root/src/delegate" ]; then
                export PYTHONPATH="$delegate_root/src''${PYTHONPATH:+:$PYTHONPATH}"
              else
                echo "delegate dev shell: no delegate working copy here, PYTHONPATH is not set" >&2
              fi
              unset delegate_root
            '';
          };
        }
      );

      # The two skills as plain directories, for a consumer that installs them.
      lib.skills = {
        agent-delegation = ./skills/agent-delegation;
        agent-definitions = ./skills/agent-definitions;
      };
    };
}
