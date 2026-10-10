"""The docs tooling: the generator of the reference sections, `reference.py`, and the neutrality check, `neutrality.py`.

This package is a supporting package. It reads the code of the other layers to write the docs and to check the kit,
and no layer but `cli`, which holds the `delegate docs` command, imports it. `tests/test_architecture.py` holds the rule.
"""
