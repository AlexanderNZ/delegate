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
          agent-definitions = pkgs.callPackage ./skills/agent-definitions/validator/package.nix { };
        in
        {
          inherit agent-definitions;
          default = agent-definitions;
        }
      );

      # The package build runs the pytest suite. With no denylist, the
      # neutrality test skips with a notice.
      checks = forAllSystems (pkgs: {
        agent-definitions = self.packages.${pkgs.stdenv.hostPlatform.system}.agent-definitions;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python313.withPackages (ps: [
              ps.pytest
              ps.pyyaml
            ]))
            pkgs.git
            pkgs.bash
          ];
        };
      });

      # The two skills as plain directories, for a consumer that installs them.
      lib.skills = {
        agent-delegation = ./skills/agent-delegation;
        agent-definitions = ./skills/agent-definitions;
      };
    };
}
