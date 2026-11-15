"""The configuration file of the user: the settings of a machine that each `delegate` process reads (#323, ADR 0008).

The file is `$XDG_CONFIG_HOME/delegate/config.toml`, or `~/.config/delegate/config.toml` when `XDG_CONFIG_HOME` is not set,
is empty, or is not an absolute path. A configuration manager can write it once for each machine, so no setting of a
shell, a service or an agent is necessary. The file is optional: with no file, the defaults apply.

The keys:

- `index`, text: the directory of a run index that more than one host shares. The default is the directory `index/` of the
  state root of this machine. `DELEGATE_INDEX`, when it is set and not empty, wins over the key.
- `hosts`, a list of text: the ssh names of the other hosts that `delegate runs` reads on this machine. The default is no host.

Each error of the file is loud: a file that cannot be read, a file that is not valid TOML, an unknown key, a value of the
wrong type, and a host of `hosts` that names this machine. `load_user_config` raises `UserConfigError` with one line that
names the file, the key and the expected form.
"""

from __future__ import annotations

import os
import socket
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .host import HOST_VARIABLE, short_host_name

CONFIG_HOME_VARIABLE: str = "XDG_CONFIG_HOME"

# The path of the file under the configuration home.
CONFIG_NAME: Path = Path("delegate") / "config.toml"

INDEX_KEY: str = "index"
HOSTS_KEY: str = "hosts"

# The expected form of each key, for the error line.
FORMS: dict[str, str] = {
    INDEX_KEY: f'{INDEX_KEY} = "/an/absolute/directory" (text: the path of a shared directory)',
    HOSTS_KEY: f'{HOSTS_KEY} = ["build-host"] (a list of the ssh names of the other hosts)',
}


class UserConfigError(Exception):
    """The configuration file cannot be used. The message is one line that names the file, the key and the expected form."""


@dataclass(frozen=True)
class UserConfig:
    """The settings of the configuration file: the file that was read, or None when there is no file, and the value of each key.

    `index` is None when the file does not set it. `hosts` is empty when the file does not set it.
    """

    path: Path | None = None
    index: Path | None = None
    hosts: tuple[str, ...] = ()


def config_path(environment: Mapping[str, str]) -> Path:
    """The path of the configuration file: under `XDG_CONFIG_HOME` when it is an absolute path, else under `~/.config`."""
    base = environment.get(CONFIG_HOME_VARIABLE, "")
    home = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return home / CONFIG_NAME


def machine_names(environment: Mapping[str, str]) -> frozenset[str]:
    """The names of this machine, in lower case: `localhost`, the host name, the short host name, and `DELEGATE_HOST` when it is set."""
    names = {"localhost", socket.gethostname(), short_host_name(), environment.get(HOST_VARIABLE, "")}
    return frozenset(name.lower() for name in names if name)


def _hosts(path: Path, value: object, this_machine: frozenset[str]) -> tuple[str, ...]:
    """The hosts of the key `hosts`, in the order of the file, each one time. Raise UserConfigError for a value that is not a list of names."""
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise UserConfigError(f"config {path}: {HOSTS_KEY} must be a list of host names, not {value!r}; write {FORMS[HOSTS_KEY]}")
    for item in value:
        if item != item.strip() or item.startswith("-") or "/" in item:
            raise UserConfigError(f"config {path}: {HOSTS_KEY} has {item!r}, which is not an ssh host name; write {FORMS[HOSTS_KEY]}")
        if item.lower() in this_machine:
            raise UserConfigError(
                f"config {path}: {HOSTS_KEY} has {item!r}, which is this machine; remove it: delegate runs always shows the runs of this machine"
            )
    return tuple(dict.fromkeys(value))


def _index(path: Path, value: object) -> Path:
    """The directory of the key `index`. Raise UserConfigError for a value that is not the text of an absolute path."""
    if not isinstance(value, str) or not value.strip():
        raise UserConfigError(f"config {path}: {INDEX_KEY} must be text, not {value!r}; write {FORMS[INDEX_KEY]}")
    directory = Path(value).expanduser()
    if not directory.is_absolute():
        raise UserConfigError(f"config {path}: {INDEX_KEY} is {value!r}, which is not an absolute path; write {FORMS[INDEX_KEY]}")
    return directory


def load_user_config(environment: Mapping[str, str] | None = None) -> UserConfig:
    """The settings of the configuration file of `environment` (the environment of the process by default). No file gives the defaults.

    Raise UserConfigError when the file cannot be read, is not valid TOML, has a key that is not `index` or `hosts`, has a
    value of the wrong type, or has a host in `hosts` that names this machine.
    """
    environment = os.environ if environment is None else environment
    path = config_path(environment)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return UserConfig()
    except (OSError, UnicodeDecodeError) as error:
        raise UserConfigError(f"config {path}: cannot read the file: {getattr(error, 'strerror', None) or error}") from None
    try:
        values = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise UserConfigError(f"config {path}: the file is not valid TOML: {error}") from None
    unknown = sorted(set(values) - set(FORMS))
    if unknown:
        known = "; ".join(FORMS[key] for key in (INDEX_KEY, HOSTS_KEY))
        raise UserConfigError(f"config {path}: unknown key {unknown[0]!r}; the keys are {known}")
    return UserConfig(
        path=path,
        index=_index(path, values[INDEX_KEY]) if INDEX_KEY in values else None,
        hosts=_hosts(path, values[HOSTS_KEY], machine_names(environment)) if HOSTS_KEY in values else (),
    )
