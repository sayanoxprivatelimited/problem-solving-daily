---
title: Layered configuration resolution
category: cli
level: hard
concepts: config precedence, dotenv parsing, type coercion, provenance, masking
status: solved
---

# Layered configuration resolution

## Context

An API works locally and connects to the wrong database in production. Nobody is
lying: `db.host` is in the repo's `config.env`, in a Kubernetes ConfigMap, in the
deployment's `env:` block, and on the command line of the process that is
actually broken. Four places can set one value, and when they disagree *nobody
can say which one won*.

The fix is not "document it harder". It is a resolver that applies the layers in
a fixed order, records who won, and refuses to guess:

```
flag  >  environment  >  .env file  >  default
```

## Behaviour

```bash
python -m solution.cli                                  # explain table, masked
python -m solution.cli --json                           # machine readable, masked
python -m solution.cli --config prod.env --db-port 5433 # flag beats the file
python -m solution.cli --set web.workers=9              # set without a shell flag
python -m solution.cli --json --reveal-secrets          # only when you mean it
```

```text
db.host     = db.internal                  (env:APP_DB__HOST)
db.port     = 5433                         (flag:--db-port)
db.user     = app                          (default)
db.password = ***5b4a                      (default)
```

Naming is mechanical: `db.host` is `APP_DB__HOST` in the environment (dots become
`__` under a prefix) and `--db-host` on the command line.

## Requirements

| # | Requirement | Why it matters |
|---|-------------|----------------|
| 1 | Precedence is exactly `flag > env > file > default` | a documented order is the whole product |
| 2 | Every resolved value carries its origin: `default`, `file:<name>:<line>`, `env:<VAR>`, `flag:<--flag>` | "why is it 5433?" answered without reading four files |
| 3 | A bad value fails with the origin in the message | `db.port: expected a whole number, got 'eighty' (from env:APP_DB__PORT)` |
| 4 | An unknown key in the file or on the command line is an error, with a suggestion | a typo must not silently fall back to a default |
| 5 | The environment is *not* checked for unknown variables | it belongs to the whole machine, not to this app |
| 6 | `.env` parsing: `#` comments, blank lines, `export`, spaces around `=`, single quotes (literal), double quotes (`\n \t \r \" \\`), trailing ` #` comments on unquoted values | every dotenv file in the world uses all of these |
| 7 | A malformed line is an error that names the file and the line number | "invalid config" costs an hour; `config.env:7` costs a minute |
| 8 | Duplicate keys in a file: the last one wins | matches every shell and dotenv loader |
| 9 | Coercion is strict: `int` accepts only `[+-]?\d+`, `bool` only `true/false/yes/no/on/off/1/0`, `list` splits on commas | `"5432 "` and `"8O80"` must not both "work" |
| 10 | `str` values are kept verbatim — no trimming | a password may begin or end with a space |
| 11 | An empty value is a value, not "unset" | `APP_DB__PORT=` is an error; delete the variable to fall through |
| 12 | Secrets are masked by default, `***` plus the last four characters when the value is longer than eight | config dumps end up in tickets and Slack |
| 13 | Flags: `--key value`, `--key=value`, booleans `--flag` / `--no-flag` / `--flag=false`, lists repeatable (`--api-tags a --api-tags b`) | the four forms people actually type |
| 14 | The CLI exits `0` on success, `2` on any configuration error, with one `error:` line on stderr | scripts can tell "bad config" from "bad server" |

## Constraints & acceptance criteria

- `resolve()` is pure: it takes `file_values`, `env` and `flags` mappings and
  never touches `os.environ`. Only `load_config()` reads the process environment
  and the filesystem, so every rule above is testable without monkey-patching.
- The returned object exposes `values`, `sources` and `as_dict()`; `as_dict()`
  masks secrets unless `reveal_secrets=True`.
- Schema order is preserved in both the dict and the explain table.
- Leftover non-flag arguments are returned by `parse_flags()` and rejected by the
  CLI — a silent `serve` argument is how people configure the wrong thing.
- Round trip: a value that came from a file and a value that came from an
  environment variable of the same spelling resolve to the same typed object.

## Assumptions (write them down — real problems are under-specified)

- The `.env` file uses **full environment variable names** (`APP_DB__HOST=…`),
  so the same file works for anyone who exports it.
- Interpolation (`${OTHER}`) is out of scope: it needs a resolver inside the
  file parser and it is where dotenv specifications disagree most.
- Nested structures stop at one level: `api.tags` is a comma separated list, not
  YAML.
- An unquoted `#` only starts a comment when preceded by a space, so
  `TOKEN=#fragment` keeps its `#`.
- A file that is passed with `--config` must exist; a missing file is an error,
  not a silent "no file layer".
- A boolean flag takes no separate value: `--web-debug false` means *true* and
  leaves `false` as a leftover argument, which the CLI rejects. Write
  `--web-debug=false` or `--no-web-debug`.

## How to run

```bash
cd problems/cli/02-layered-configuration-resolution
python -m unittest discover -s tests -t .
printf 'APP_DB__HOST=db.internal\nAPP_DB__PORT=6000\n' > /tmp/config.env
python -m solution.cli --config /tmp/config.env --db-port 5433
APP_DB__PORT=7000 python -m solution.cli --config /tmp/config.env --json
python -m solution.cli --db-portt 5433; echo "exit: $?"    # 2, with a suggestion
```

## Stretch goals

- A `--watch` mode that reports which keys changed when the file is edited.
- JSON-schema output of the resolved config, secrets represented as `{"secret": true}`.
- `--check-env`: warn about `APP_-prefixed` variables that shadow a file value
  (the highest-value diagnostic in a real deployment).
