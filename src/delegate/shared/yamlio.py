"""The one place that loads and dumps YAML (#345).

PyYAML has a C loader and a C dumper when it was built with libyaml. They are about 30 times faster than the pure-Python ones
(a frontmatter of 30 KB: 0.37 ms against 12.1 ms) and they write the same text. This module takes the C class when `yaml` has it,
and the Python class when it has not. The class is looked up at each call, so a test can remove the C class and see the other path.
"""

from __future__ import annotations

from typing import Any

import yaml


def load(text: str) -> Any:
    """Parse `text` as safe YAML, with the C loader when `yaml` has it. Raise `yaml.YAMLError` for a text that does not parse."""
    return yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


def dump(data: Any, width: int) -> str:
    """Write `data` as block-style YAML with the keys in their order and no escape for a non-ASCII letter.

    `width` is the column where a long plain scalar folds. A value that must stay on one line gets a width that no line reaches.
    """
    return yaml.dump(
        data,
        Dumper=getattr(yaml, "CSafeDumper", yaml.SafeDumper),
        sort_keys=False,
        allow_unicode=True,
        width=width,
        default_flow_style=False,
    )
