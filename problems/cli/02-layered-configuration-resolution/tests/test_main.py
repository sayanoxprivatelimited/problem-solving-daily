"""Tests for layered config: dotenv parsing, coercion, precedence, provenance, CLI."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from solution import cli
from solution.main import (
    DEFAULT_SCHEMA,
    ConfigError,
    Option,
    SourceLine,
    apply_settings,
    coerce,
    env_var_name,
    flag_name,
    load_config,
    mask_value,
    parse_env_file,
    parse_flags,
    render_explain,
    resolve,
    unknown_key_error,
)

SMALL = (
    Option("db.host", "str", "localhost"),
    Option("db.port", "int", 5432),
    Option("db.password", "str", "", secret=True),
    Option("api.tags", "list", ()),
    Option("web.debug", "bool", False),
)


class ParseEnvFileTest(unittest.TestCase):
    def test_simple_pairs_keep_their_line_numbers(self) -> None:
        parsed = parse_env_file("# head\nA=1\n\nB = two\n")
        self.assertEqual(parsed["A"], SourceLine("1", 2))
        self.assertEqual(parsed["B"], SourceLine("two", 4))

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        parsed = parse_env_file("# a comment\n   \n\t\nA=1\n")
        self.assertEqual(list(parsed), ["A"])

    def test_export_prefix_is_accepted(self) -> None:
        parsed = parse_env_file("export A=1\nexport   B=2\n")
        self.assertEqual(parsed["A"].value, "1")
        self.assertEqual(parsed["B"].value, "2")

    def test_double_quotes_decode_escapes(self) -> None:
        parsed = parse_env_file(r'A="line\nnext\ttab\"quote\\slash"' + "\n")
        self.assertEqual(parsed["A"].value, 'line\nnext\ttab"quote\\slash')

    def test_single_quotes_are_literal(self) -> None:
        parsed = parse_env_file("A='no \\n escape'\n")
        self.assertEqual(parsed["A"].value, "no \\n escape")

    def test_unknown_escape_names_the_line(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_env_file('A="bad \\q"\n', source="config.env")
        self.assertIn("config.env:1", str(caught.exception))
        self.assertIn("unknown escape", str(caught.exception))

    def test_unterminated_quote_is_an_error(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_env_file('A="never closed\n')
        self.assertIn("unterminated", str(caught.exception))

    def test_trailing_comment_on_an_unquoted_value(self) -> None:
        self.assertEqual(parse_env_file("A=8080 # the port\n")["A"].value, "8080")

    def test_hash_without_a_space_belongs_to_the_value(self) -> None:
        self.assertEqual(parse_env_file("A=#fragment\n")["A"].value, "#fragment")

    def test_quoted_value_may_be_followed_by_a_comment(self) -> None:
        self.assertEqual(parse_env_file('A="x y" # note\n')["A"].value, "x y")

    def test_junk_after_a_quoted_value_is_rejected(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_env_file('A="x" y\n')
        self.assertIn("after a quoted value", str(caught.exception))

    def test_missing_equals_is_an_error_with_a_line(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_env_file("A=1\nJUST_A_KEY\n", source="config.env")
        self.assertIn("config.env:2", str(caught.exception))
        self.assertIn("expected KEY=VALUE", str(caught.exception))

    def test_invalid_key_is_rejected(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_env_file("2BAD=1\n")
        self.assertIn("not a valid key", str(caught.exception))

    def test_duplicate_key_last_wins(self) -> None:
        parsed = parse_env_file("A=1\nA=2\n")
        self.assertEqual(parsed["A"], SourceLine("2", 2))

    def test_empty_value_is_allowed(self) -> None:
        self.assertEqual(parse_env_file("A=\n")["A"].value, "")


class CoerceTest(unittest.TestCase):
    def test_int_accepts_surrounding_space_and_signs(self) -> None:
        port = Option("db.port", "int")
        self.assertEqual(coerce(port, " 5432 "), 5432)
        self.assertEqual(coerce(port, "-1"), -1)

    def test_int_rejects_everything_that_is_not_a_whole_number(self) -> None:
        port = Option("db.port", "int")
        for raw in ("5432.0", "8O80", "0x10", "", "1_000", "1e3"):
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                coerce(port, raw)

    def test_int_error_names_the_origin(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            coerce(Option("db.port", "int"), "eighty", source="env:APP_DB__PORT")
        self.assertEqual(
            str(caught.exception),
            "db.port: expected a whole number, got 'eighty' (from env:APP_DB__PORT)",
        )

    def test_bool_spellings(self) -> None:
        flag = Option("web.debug", "bool")
        for raw in ("1", "true", "TRUE", "Yes", "on"):
            with self.subTest(raw=raw):
                self.assertIs(coerce(flag, raw), True)
        for raw in ("0", "false", "No", "OFF"):
            with self.subTest(raw=raw):
                self.assertIs(coerce(flag, raw), False)

    def test_bool_error_lists_the_accepted_spellings(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            coerce(Option("web.debug", "bool"), "probably")
        message = str(caught.exception)
        self.assertIn("expected a boolean", message)
        self.assertIn("yes/no", message)

    def test_list_splits_strips_and_drops_empties(self) -> None:
        tags = Option("api.tags", "list")
        self.assertEqual(coerce(tags, "a, b ,, c"), ("a", "b", "c"))
        self.assertEqual(coerce(tags, ""), ())
        self.assertEqual(coerce(tags, " , "), ())

    def test_str_keeps_its_padding(self) -> None:
        """A password may legitimately start or end with a space; only int/bool/list strip."""
        self.assertEqual(coerce(Option("db.password", "str"), "  spaced  "), "  spaced  ")

    def test_unknown_kind_is_refused_at_definition_time(self) -> None:
        with self.assertRaises(ValueError):
            Option("x.y", "float")


class NamingTest(unittest.TestCase):
    def test_env_names_use_a_double_underscore_for_nesting(self) -> None:
        self.assertEqual(env_var_name("db.host"), "APP_DB__HOST")
        self.assertEqual(env_var_name("feature.new_ui", prefix="SVC_"), "SVC_FEATURE__NEW_UI")

    def test_flag_names_use_dashes(self) -> None:
        self.assertEqual(flag_name("db.port"), "--db-port")
        self.assertEqual(flag_name("feature.new_ui"), "--feature-new-ui")


class PrecedenceTest(unittest.TestCase):
    def test_default_when_no_layer_sets_anything(self) -> None:
        resolved = resolve(SMALL, env={})
        self.assertEqual(resolved["db.port"], 5432)
        self.assertEqual(resolved.source("db.port"), "default")

    def test_file_beats_default(self) -> None:
        resolved = resolve(SMALL, file_values={"APP_DB__PORT": "6000"}, env={})
        self.assertEqual(resolved["db.port"], 6000)
        self.assertEqual(resolved.source("db.port"), "file:config.env")

    def test_env_beats_file(self) -> None:
        resolved = resolve(
            SMALL, file_values={"APP_DB__PORT": "6000"}, env={"APP_DB__PORT": "7000"}
        )
        self.assertEqual(resolved["db.port"], 7000)
        self.assertEqual(resolved.source("db.port"), "env:APP_DB__PORT")

    def test_flag_beats_everything(self) -> None:
        resolved = resolve(
            SMALL,
            file_values={"APP_DB__PORT": "6000"},
            env={"APP_DB__PORT": "7000"},
            flags={"db.port": "8000"},
        )
        self.assertEqual(resolved["db.port"], 8000)
        self.assertEqual(resolved.source("db.port"), "flag:--db-port")

    def test_every_layer_at_once_has_the_expected_provenance(self) -> None:
        resolved = resolve(
            SMALL,
            file_values=parse_env_file("APP_DB__PORT=6000\nAPP_WEB__DEBUG=true\n"),
            env={"APP_DB__HOST": "db.internal"},
            flags={"db.port": "8000"},
        )
        self.assertEqual(
            resolved.sources,
            {
                "db.host": "env:APP_DB__HOST",
                "db.port": "flag:--db-port",
                "db.password": "default",
                "api.tags": "default",
                "web.debug": "file:config.env:2",
            },
        )

    def test_an_empty_environment_value_is_a_value_not_a_fall_through(self) -> None:
        resolved = resolve(SMALL, file_values={"APP_DB__HOST": "from-file"}, env={"APP_DB__HOST": ""})
        self.assertEqual(resolved["db.host"], "")
        self.assertEqual(resolved.source("db.host"), "env:APP_DB__HOST")

    def test_an_empty_value_for_a_typed_option_fails_and_says_where_it_came_from(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            resolve(SMALL, env={"APP_DB__PORT": ""})
        self.assertIn("(from env:APP_DB__PORT)", str(caught.exception))

    def test_file_line_numbers_appear_in_the_source(self) -> None:
        parsed = parse_env_file("# a comment\nAPP_DB__PORT=6000\n", source="config.env")
        resolved = resolve(SMALL, file_values=parsed, env={})
        self.assertEqual(resolved.source("db.port"), "file:config.env:2")

    def test_missing_key_lookups_name_the_key(self) -> None:
        resolved = resolve(SMALL, env={})
        with self.assertRaises(KeyError) as caught:
            resolved["nope.nothing"]
        self.assertIn("nope.nothing", str(caught.exception))


class UnknownKeyTest(unittest.TestCase):
    def test_unknown_file_key_gets_a_suggestion_and_a_line(self) -> None:
        parsed = parse_env_file("APP_DB__HOTS=h\n", source="config.env")
        with self.assertRaises(ConfigError) as caught:
            resolve(SMALL, file_values=parsed, env={})
        self.assertEqual(
            str(caught.exception),
            "file:config.env:1: unknown key 'APP_DB__HOTS' (did you mean APP_DB__HOST?)",
        )

    def test_unknown_flag_layer_key_is_an_error(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            resolve(SMALL, env={}, flags={"db.hots": "h"})
        self.assertIn("did you mean db.host", str(caught.exception))

    def test_no_suggestion_when_nothing_is_close(self) -> None:
        error = unknown_key_error("COMPLETELY_DIFFERENT", ["APP_DB__HOST"], where="config.env:1")
        self.assertNotIn("did you mean", str(error))
        self.assertIn("unknown key", str(error))


class ParseFlagsTest(unittest.TestCase):
    def test_inline_and_separate_values(self) -> None:
        flags, rest = parse_flags(SMALL, ["--db-port=5433", "--db-host", "db.internal"])
        self.assertEqual(flags, {"db.port": "5433", "db.host": "db.internal"})
        self.assertEqual(rest, [])

    def test_boolean_flags_need_no_value(self) -> None:
        flags, _ = parse_flags(SMALL, ["--web-debug"])
        self.assertEqual(flags, {"web.debug": "true"})
        flags, _ = parse_flags(SMALL, ["--no-web-debug"])
        self.assertEqual(flags, {"web.debug": "false"})
        flags, _ = parse_flags(SMALL, ["--web-debug=false"])
        self.assertEqual(flags, {"web.debug": "false"})

    def test_repeated_list_flags_are_joined(self) -> None:
        flags, _ = parse_flags(SMALL, ["--api-tags", "a", "--api-tags", "b,c"])
        self.assertEqual(flags, {"api.tags": "a,b,c"})

    def test_unknown_flag_suggests_a_real_one(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_flags(SMALL, ["--db-portt", "5433"])
        message = str(caught.exception)
        self.assertIn("unknown flag '--db-portt'", message)
        self.assertIn("did you mean --db-port?", message)

    def test_a_missing_value_is_an_error(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_flags(SMALL, ["--db-port"])
        self.assertIn("needs a value", str(caught.exception))

    def test_leftovers_are_returned_for_the_caller_to_reject(self) -> None:
        flags, rest = parse_flags(SMALL, ["serve", "--db-port", "1", "--", "--not-a-flag"])
        self.assertEqual(flags, {"db.port": "1"})
        self.assertEqual(rest, ["serve", "--not-a-flag"])

    def test_negated_form_is_only_for_booleans(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_flags(SMALL, ["--no-db-host", "db.internal"])
        self.assertIn("unknown flag '--no-db-host'", str(caught.exception))

    def test_a_negated_boolean_refuses_a_value(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_flags(SMALL, ["--no-web-debug=true"])
        self.assertIn("takes no value", str(caught.exception))

    def test_a_boolean_does_not_swallow_the_next_argument(self) -> None:
        """``--web-debug false`` is not a spelling: the bare flag means true, and
        ``false`` is left over for the caller to reject."""
        flags, rest = parse_flags(SMALL, ["--web-debug", "false"])
        self.assertEqual(flags, {"web.debug": "true"})
        self.assertEqual(rest, ["false"])


class OutputTest(unittest.TestCase):
    def test_mask_value_keeps_the_last_four_characters_only_when_worth_it(self) -> None:
        self.assertEqual(mask_value(""), "***")
        self.assertEqual(mask_value("hunter2"), "***")
        self.assertEqual(mask_value("sk_live_9f8e7d6c5b4a"), "***5b4a")

    def test_as_dict_masks_secrets_by_default(self) -> None:
        resolved = resolve(SMALL, env={"APP_DB__PASSWORD": "sk_live_9f8e7d6c5b4a"})
        self.assertEqual(resolved.as_dict()["db.password"], "***5b4a")
        self.assertEqual(
            resolved.as_dict(reveal_secrets=True)["db.password"], "sk_live_9f8e7d6c5b4a"
        )

    def test_render_explain_aligns_names_and_shows_the_source(self) -> None:
        resolved = resolve(SMALL, env={"APP_DB__PORT": "7000"})
        lines = render_explain(resolved).splitlines()
        self.assertEqual(len(lines), len(SMALL))
        self.assertTrue(lines[0].startswith("db.host"))
        self.assertTrue(lines[1].startswith("db.port "))
        self.assertIn("7000", lines[1])
        self.assertIn("(env:APP_DB__PORT)", lines[1])
        self.assertIn("(default)", lines[0])
        self.assertIn("***", render_explain(resolve(SMALL, env={"APP_DB__PASSWORD": "s3cret!"})))

    def test_apply_settings_parses_pairs(self) -> None:
        self.assertEqual(apply_settings(["db.port=5433", " db.host=x"]), {"db.port": "5433", "db.host": "x"})

    def test_apply_settings_rejects_junk(self) -> None:
        with self.assertRaises(ConfigError):
            apply_settings(["db.port"])


class LoadConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.env"
        self.path.write_text("APP_DB__PORT=6000\nAPP_DB__HOST=file-host\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_the_file_and_lets_the_environment_win(self) -> None:
        resolved = load_config(self.path, schema=SMALL, env={"APP_DB__HOST": "env-host"})
        self.assertEqual(resolved["db.port"], 6000)
        self.assertEqual(resolved["db.host"], "env-host")
        self.assertEqual(resolved.source("db.port"), f"file:{self.path}:1")

    def test_a_missing_file_raises_instead_of_being_ignored(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config(Path(self._tmp.name) / "nope.env", schema=SMALL, env={})


class CliTest(unittest.TestCase):
    def run_cli(self, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_json_output_is_masked(self) -> None:
        code, out, _ = self.run_cli(["--json"], env={"APP_DB__PASSWORD": "sk_live_9f8e7d6c5b4a"})
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["db.password"], "***5b4a")
        self.assertEqual(payload["db.port"], 5432)

    def test_reveal_secrets_shows_the_password(self) -> None:
        code, out, _ = self.run_cli(
            ["--json", "--reveal-secrets"], env={"APP_DB__PASSWORD": "sk_live_9f8e7d6c5b4a"}
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["db.password"], "sk_live_9f8e7d6c5b4a")

    def test_a_schema_key_works_as_a_flag(self) -> None:
        code, out, _ = self.run_cli(["--json", "--db-port", "5433"], env={})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["db.port"], 5433)

    def test_set_beats_the_environment(self) -> None:
        code, out, _ = self.run_cli(["--json", "--set", "web.workers=9"], env={"APP_WEB__WORKERS": "2"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["web.workers"], 9)

    def test_explain_is_the_default_output(self) -> None:
        code, out, _ = self.run_cli([], env={"APP_DB__HOST": "db.internal"})
        self.assertEqual(code, 0)
        self.assertIn("db.host", out)
        self.assertIn("(env:APP_DB__HOST)", out)

    def test_unknown_flag_exits_two_with_a_message(self) -> None:
        code, _, err = self.run_cli(["--db-portt", "1"], env={})
        self.assertEqual(code, 2)
        self.assertIn("did you mean --db-port?", err)

    def test_bad_value_exits_two_and_names_the_variable(self) -> None:
        code, _, err = self.run_cli([], env={"APP_DB__PORT": "eighty"})
        self.assertEqual(code, 2)
        self.assertIn("APP_DB__PORT", err)

    def test_missing_config_file_exits_two(self) -> None:
        code, _, err = self.run_cli(["--config", "/nope/config.env"], env={})
        self.assertEqual(code, 2)
        self.assertIn("no such config file", err)

    def test_positional_arguments_are_rejected(self) -> None:
        code, _, err = self.run_cli(["serve"], env={})
        self.assertEqual(code, 2)
        self.assertIn("unexpected argument", err)

    def test_config_file_is_read_when_given(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.env"
            path.write_text("APP_API__TAGS=a,b\n", encoding="utf-8")
            code, out, _ = self.run_cli(["--json", "--config", str(path)], env={})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["api.tags"], ["a", "b"])


class SchemaTest(unittest.TestCase):
    def test_the_default_schema_is_internally_consistent(self) -> None:
        names = [option.name for option in DEFAULT_SCHEMA]
        self.assertEqual(len(names), len(set(names)), "duplicate option names")
        groups = [name.split(".")[0] for name in names]
        runs = [group for index, group in enumerate(groups) if index == 0 or groups[index - 1] != group]
        self.assertEqual(
            len(runs), len(set(runs)), "related keys must sit together in the explain table"
        )

    def test_defaults_match_their_declared_kind(self) -> None:
        for option in DEFAULT_SCHEMA:
            with self.subTest(option=option.name):
                if option.kind == "int":
                    self.assertIsInstance(option.default, int)
                elif option.kind == "bool":
                    self.assertIsInstance(option.default, bool)
                elif option.kind == "list":
                    self.assertIsInstance(option.default, tuple)
                else:
                    self.assertIsInstance(option.default, str)

    def test_a_default_is_used_verbatim_not_reparsed(self) -> None:
        resolved = resolve(DEFAULT_SCHEMA, env={})
        self.assertEqual(resolved["api.tags"], ())
        self.assertIs(resolved["web.debug"], False)


if __name__ == "__main__":
    unittest.main()
