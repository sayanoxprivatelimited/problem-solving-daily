"""Resolve one config value from four places — and say which place won.

Every service ends up with the same four layers, in this order of authority:

    flag  >  environment  >  .env file  >  default

The rules this module refuses to break:

* **Provenance is a first-class value.** ``Resolved.sources`` answers "why is
  ``db.port`` 5433?" without anyone re-reading four files at 3 a.m.
* **A bad value names its origin.** Errors say ``(from env:APP_DB__PORT)``, not
  just "invalid configuration".
* **Nothing is guessed silently.** An unknown key in the file or on the command
  line is an error with a suggestion; the environment is *not* checked for
  unknown variables, because it belongs to the whole machine.
* **An empty value is a value.** ``APP_DB__PORT=`` is an error for an ``int``,
  and ``""`` for a ``str``. Unset the variable to fall through — do not empty it.
* **Secrets do not leak by default.** ``Resolved.as_dict()`` masks them.

Example
-------
>>> env_var_name("db.host")
'APP_DB__HOST'
>>> flag_name("web.debug")
'--web-debug'
>>> parse_env_file('export APP_DB__HOST = 10.0.0.1  # primary\\n')["APP_DB__HOST"].value
'10.0.0.1'
>>> coerce(Option("db.port", "int"), " 5432 ")
5432
>>> coerce(Option("web.debug", "bool"), "YES")
True
>>> coerce(Option("api.tags", "list"), "a, b ,, c")
('a', 'b', 'c')
>>> mask_value("sk_live_9f8e7d6c5b4a")
'***5b4a'
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_PREFIX = "APP_"
DEFAULT_FILE_NAME = "config.env"
KINDS = ("str", "int", "bool", "list")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_INT_RE = re.compile(r"^[+-]?\d+$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class ConfigError(ValueError):
    """A configuration problem a human can fix: bad value, unknown key, bad line."""


# --------------------------------------------------------------------- schema --


@dataclass(frozen=True)
class Option:
    """One configurable value: its name, its type, and what it falls back to."""

    name: str
    kind: str = "str"
    default: Any = ""
    secret: bool = False
    help: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: unknown kind {self.kind!r} (use one of {', '.join(KINDS)})")
        if not self.name or not all(part.replace("_", "").isalnum() for part in self.name.split(".")):
            raise ValueError(f"{self.name!r} is not a dotted config name")


DEFAULT_SCHEMA: tuple[Option, ...] = (
    Option("db.host", "str", "localhost", help="postgres host"),
    Option("db.port", "int", 5432, help="postgres port"),
    Option("db.user", "str", "app"),
    Option("db.password", "str", "", secret=True),
    Option("api.url", "str", "http://localhost:8000", help="upstream base URL"),
    Option("api.timeout", "int", 10, help="seconds"),
    Option("api.retries", "int", 3),
    Option("api.tags", "list", (), help="comma separated, repeatable"),
    Option("web.workers", "int", 4),
    Option("web.debug", "bool", False),
    Option("feature.new_ui", "bool", False),
)


# ------------------------------------------------------------------- naming --


def env_var_name(name: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Environment variable for a dotted name: dots become ``__``.

    >>> env_var_name("feature.new_ui")
    'APP_FEATURE__NEW_UI'
    """
    return prefix + name.replace(".", "__").upper()


def flag_name(name: str) -> str:
    """Command-line flag for a dotted name: dots and underscores become dashes.

    >>> flag_name("db.port"), flag_name("feature.new_ui")
    ('--db-port', '--feature-new-ui')
    """
    return "--" + name.replace(".", "-").replace("_", "-")


# ---------------------------------------------------------------------- file --


@dataclass(frozen=True)
class SourceLine:
    """A value parsed out of a file, with the line it came from."""

    value: str
    line: int


def _unquote(raw: str, where: str) -> str:
    """Decode a right-hand side, returning the bare value."""
    text = raw.strip()
    if not text:
        return ""
    quote = text[0]
    if quote not in "\"'":
        # An unquoted value ends at the first " #"; anything else is the value.
        cut = text.find(" #")
        return (text if cut < 0 else text[:cut]).strip()

    out: list[str] = []
    index = 1
    escapes = quote == '"'
    while index < len(text):
        char = text[index]
        if char == "\\" and escapes:
            index += 1
            if index >= len(text):
                raise ConfigError(f"{where}: a backslash at the end of the line escapes nothing")
            escaped = text[index]
            if escaped not in _ESCAPES:
                raise ConfigError(f"{where}: unknown escape \\{escaped} in a double-quoted value")
            out.append(_ESCAPES[escaped])
        elif char == quote:
            rest = text[index + 1 :].strip()
            if rest and not rest.startswith("#"):
                raise ConfigError(f"{where}: unexpected {rest!r} after a quoted value")
            return "".join(out)
        else:
            out.append(char)
        index += 1
    raise ConfigError(f"{where}: unterminated {quote}-quoted value")


def parse_env_file(text: str, *, source: str = DEFAULT_FILE_NAME) -> dict[str, SourceLine]:
    """Parse dotenv text into ``KEY -> (value, line)``.

    Supported: blank lines, ``#`` comments, ``export`` prefixes, ``=`` with
    spaces around it, single quotes (literal), double quotes (``\\n``, ``\\t``,
    ``\\r``, ``\\"``, ``\\\\``), trailing `` #`` comments on unquoted values, and
    duplicate keys where the last one wins.

    >>> parse_env_file('A=1\\nB="two words"  # note\\n').keys()
    dict_keys(['A', 'B'])
    >>> parse_env_file("A='no \\\\n escape'\\n")["A"].value
    'no \\\\n escape'
    """
    values: dict[str, SourceLine] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        where = f"{source}:{number}"
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator:
            raise ConfigError(f"{where}: expected KEY=VALUE, got {line!r}")
        key = key.strip()
        if not _KEY_RE.match(key):
            raise ConfigError(f"{where}: {key!r} is not a valid key (letters, digits and _ only)")
        values[key] = SourceLine(_unquote(raw_value, where), number)
    return values


# ------------------------------------------------------------------- coerce --


def _from(source: str | None) -> str:
    return f" (from {source})" if source else ""


def coerce(option: Option, raw: str, *, source: str | None = None) -> Any:
    """Turn a string from a flag, the environment or a file into a typed value.

    >>> coerce(Option("web.debug", "bool"), "off")
    False
    >>> coerce(Option("api.tags", "list"), "")
    ()
    """
    text = raw.strip() if option.kind != "str" else raw
    if option.kind == "str":
        return text
    if option.kind == "int":
        if not _INT_RE.match(text):
            raise ConfigError(
                f"{option.name}: expected a whole number, got {raw!r}{_from(source)}"
            )
        return int(text)
    if option.kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ConfigError(
            f"{option.name}: expected a boolean (true/false, yes/no, on/off, 1/0), "
            f"got {raw!r}{_from(source)}"
        )
    items = tuple(part.strip() for part in text.split(",") if part.strip())
    return items


# ------------------------------------------------------------------ suggest --


def unknown_key_error(name: str, known: Sequence[str], *, where: str, what: str = "key") -> ConfigError:
    """A "did you mean" error — the cheapest way to stop a 20-minute typo hunt.

    >>> print(unknown_key_error("APP_DB__HOTS", ["APP_DB__HOST"], where="config.env:3"))
    config.env:3: unknown key 'APP_DB__HOTS' (did you mean APP_DB__HOST?)
    """
    match = get_close_matches(name, list(known), n=1)
    hint = f" (did you mean {match[0]}?)" if match else ""
    return ConfigError(f"{where}: unknown {what} {name!r}{hint}")


# -------------------------------------------------------------------- flags --


def parse_flags(schema: Sequence[Option], argv: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    """Split argv into ``({dotted name: raw value}, leftover)``.

    Booleans need no value (``--web-debug``); ``--no-web-debug`` is the same
    flag with ``false``. Lists may be repeated (``--api-tags a --api-tags b``)
    and are joined with commas. Everything that is not a flag is returned as
    leftover, so the caller can reject it.

    >>> parse_flags(DEFAULT_SCHEMA, ["--db-port=5433", "--no-web-debug"])[0]
    {'db.port': '5433', 'web.debug': 'false'}
    """
    by_flag = {flag_name(option.name): option for option in schema}
    flags: dict[str, str] = {}
    leftovers: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            leftovers.extend(argv[index + 1 :])
            break
        if not token.startswith("--"):
            leftovers.append(token)
            index += 1
            continue

        name, has_inline, inline = token.partition("=")
        option = by_flag.get(name)
        negated = False
        if option is None and name.startswith("--no-"):
            positive = "--" + name[len("--no-") :]
            candidate = by_flag.get(positive)
            # --no- is only a spelling of a boolean flag; --no-db-host is a typo,
            # not "set db.host from the next argument".
            if candidate is not None and candidate.kind != "bool":
                raise unknown_key_error(name, list(by_flag), where="command line", what="flag")
            option, negated = candidate, candidate is not None
        if option is None:
            raise unknown_key_error(name, list(by_flag), where="command line", what="flag")

        if option.kind == "bool":
            if negated:
                if has_inline:
                    raise ConfigError(
                        f"command line: flag {name} takes no value (it already means false)"
                    )
                value = "false"
            elif has_inline:
                value = inline
            else:
                value = "true"
        else:
            if has_inline:
                value = inline
            else:
                if index + 1 >= len(argv):
                    raise ConfigError(f"command line: flag {name} needs a value")
                index += 1
                value = argv[index]

        if option.kind == "list" and option.name in flags:
            flags[option.name] = f"{flags[option.name]},{value}"
        else:
            flags[option.name] = value
        index += 1
    return flags, leftovers


# ------------------------------------------------------------------- resolve --


@dataclass(frozen=True)
class Resolved:
    """Values plus where each one came from."""

    values: Mapping[str, Any]
    sources: Mapping[str, str]
    schema: tuple[Option, ...] = field(default_factory=tuple)

    def __getitem__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError:
            raise KeyError(f"{name!r} is not in the schema") from None

    def source(self, name: str) -> str:
        """Where the value came from: ``default``, ``file:…``, ``env:…`` or ``flag:…``.

        >>> resolved = resolve(DEFAULT_SCHEMA, env={"APP_DB__HOST": "db.internal"})
        >>> resolved.source("db.host"), resolved.source("db.user")
        ('env:APP_DB__HOST', 'default')
        """
        return self.sources[name]

    def as_dict(self, *, reveal_secrets: bool = False) -> dict[str, Any]:
        """A plain dict. Secrets are masked unless explicitly revealed."""
        out: dict[str, Any] = {}
        for option in self.schema:
            value = self.values[option.name]
            out[option.name] = mask_value(value) if option.secret and not reveal_secrets else value
        return out


def resolve(
    schema: Sequence[Option] = DEFAULT_SCHEMA,
    *,
    file_values: Mapping[str, str | SourceLine] | None = None,
    env: Mapping[str, str] | None = None,
    flags: Mapping[str, str] | None = None,
    prefix: str = DEFAULT_PREFIX,
    source_name: str = DEFAULT_FILE_NAME,
) -> Resolved:
    """Apply ``flag > env > file > default`` and record the winner for every key.

    >>> resolved = resolve(
    ...     DEFAULT_SCHEMA,
    ...     file_values={"APP_DB__PORT": "6000"},
    ...     env={"APP_DB__PORT": "7000"},
    ...     flags={"db.port": "8000"},
    ... )
    >>> resolved["db.port"], resolved.source("db.port")
    (8000, 'flag:--db-port')
    """
    file_values = file_values or {}
    env = {} if env is None else env
    flags = flags or {}

    by_var = {env_var_name(option.name, prefix): option for option in schema}
    by_name = {option.name: option for option in schema}

    file_layer: dict[str, tuple[str, str]] = {}
    for key, entry in file_values.items():
        if isinstance(entry, SourceLine):
            value, where = entry.value, f"file:{source_name}:{entry.line}"
        else:
            value, where = entry, f"file:{source_name}"
        if key not in by_var:
            raise unknown_key_error(key, tuple(by_var), where=where)
        file_layer[by_var[key].name] = (value, where)

    for name in flags:
        if name not in by_name:
            raise unknown_key_error(name, tuple(by_name), where="flags")

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for option in schema:
        variable = env_var_name(option.name, prefix)
        if option.name in flags:
            source = f"flag:{flag_name(option.name)}"
            values[option.name] = coerce(option, flags[option.name], source=source)
        elif variable in env:
            source = f"env:{variable}"
            values[option.name] = coerce(option, env[variable], source=source)
        elif option.name in file_layer:
            raw, source = file_layer[option.name]
            values[option.name] = coerce(option, raw, source=source)
        else:
            values[option.name] = option.default
            source = "default"
        sources[option.name] = source
    return Resolved(values=values, sources=sources, schema=tuple(schema))


def load_config(
    path: str | Path | None = None,
    *,
    schema: Sequence[Option] = DEFAULT_SCHEMA,
    env: Mapping[str, str] | None = None,
    flags: Mapping[str, str] | None = None,
    prefix: str = DEFAULT_PREFIX,
) -> Resolved:
    """Read ``path`` (if given) and resolve against the real environment."""
    file_values: dict[str, SourceLine] = {}
    source_name = DEFAULT_FILE_NAME
    if path is not None:
        source_name = str(path)
        file_values = parse_env_file(Path(path).read_text(encoding="utf-8"), source=source_name)
    return resolve(
        schema,
        file_values=file_values,
        env=os.environ if env is None else env,
        flags=flags,
        prefix=prefix,
        source_name=source_name,
    )


# ------------------------------------------------------------------- output --


def mask_value(value: Any) -> str:
    """``***`` for short secrets, ``***`` plus the last four characters for long ones.

    >>> mask_value("hunter2"), mask_value("sk_live_9f8e7d6c5b4a")
    ('***', '***5b4a')
    """
    text = str(value)
    return "***" if len(text) <= 8 else f"***{text[-4:]}"


def display_value(value: Any) -> str:
    """Render a typed value the way a config file would spell it.

    >>> display_value((1, 2)), display_value(True), display_value(())
    ('1,2', 'true', '')
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (tuple, list)):
        return ",".join(str(item) for item in value)
    return "" if value is None else str(value)


def render_explain(resolved: Resolved, *, reveal_secrets: bool = False) -> str:
    """One aligned line per key, with the layer that decided it.

    >>> print(render_explain(resolve([Option("db.port", "int", 5432)], env={})))
    db.port = 5432                         (default)
    """
    width = max((len(option.name) for option in resolved.schema), default=0)
    lines = []
    for option in resolved.schema:
        value = resolved.values[option.name]
        masked = option.secret and not reveal_secrets
        shown = mask_value(value) if masked else display_value(value)
        lines.append(f"{option.name.ljust(width)} = {shown.ljust(28)} ({resolved.sources[option.name]})")
    return "\n".join(lines)


def apply_settings(pairs: Iterable[str]) -> dict[str, str]:
    """Turn ``["db.port=5433", "api.tags=a"]`` into flag-layer values.

    >>> apply_settings(["db.port=5433"])
    {'db.port': '5433'}
    """
    flags: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            raise ConfigError(f"--set expects NAME=VALUE, got {pair!r}")
        flags[name.strip()] = value
    return flags
