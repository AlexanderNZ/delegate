"""The run context: the workflow, the engine that builds its tickets, the guards, the journal, the reports and the brief generator.

This package never imports `adapters` or `cli`, and it runs no git command. It keeps the work of a run
through the ports. `tests/test_architecture.py` holds the rules.
"""
