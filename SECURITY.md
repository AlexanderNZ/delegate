# Security policy

`delegate` runs coding agents on a local machine, with the permissions of the person who starts the run. Its guards keep an agent inside its role: a specialist does not push, a verifier does not write, and only the coordinator changes a hotspot. A way around one of those guards is a security issue.

## Supported versions

Only the newest release gets security fixes. The project is before 1.0, so a fix comes in a new release, not in a patch to an old one.

## Report a vulnerability

Do not open a public issue for a vulnerability. Report it privately:

1. Open the **Security** tab of [the repository](https://github.com/AlexanderNZ/delegate).
2. Select **Report a vulnerability**.
3. Say what the problem is, how to reproduce it, the version of `delegate`, and the harness and its version.

Only the maintainers see the report. The project has one maintainer, so there is no fixed time for an answer. The maintainer answers on the report, and credits the reporter in the advisory unless the reporter asks otherwise.

## Known limits

[The enforcement model and its limits](https://alexandernz.github.io/delegate/explanation/the-enforcement-model-and-its-limits/) lists each limit of the guards that the project knows and accepts, such as `git push --no-verify` and a verifier that writes to a file that git ignores. A limit on that page is not a vulnerability. A way around a guard that the page does not list is one, and so is a way to make the engine run a command that the workflow did not name.

`delegate` is not a sandbox. If a policy needs an agent to run in isolation from the machine, run the harness in a container.
