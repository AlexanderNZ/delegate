"""The command-line drivers: the `delegate` command and the standalone commands `agent-definitions` and `verifier-brief`.

A driver parses its arguments, makes the adapters (the git backend of the version-control port, the harness
adapter), calls a use case and prints the result. No module outside this package parses command-line arguments.
`tests/test_architecture.py` holds the rules.
"""
