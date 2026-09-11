"""Tests for slash command parsing."""

from gaia.config import SLASH_COMMANDS, parse_slash_command


class TestParseSlashCommand:
    """Tests for the parse_slash_command function."""

    def test_splits_cmd_and_arg(self) -> None:
        """A command with an arg returns both parts."""
        cmd, arg = parse_slash_command("/model gpt-4o")
        assert cmd == "model"
        assert arg == "gpt-4o"

    def test_returns_empty_arg_when_none(self) -> None:
        """A bare command returns an empty arg string."""
        cmd, arg = parse_slash_command("/help")
        assert cmd == "help"
        assert arg == ""

    def test_strips_leading_slash(self) -> None:
        """The leading / is always stripped."""
        for cmd_str in ("/clear", "/status", "/settings"):
            cmd, _ = parse_slash_command(cmd_str)
            assert not cmd.startswith("/")

    def test_lowercases_command(self) -> None:
        """Commands are always lowercased."""
        cmd, _ = parse_slash_command("/MODEL")
        assert cmd == "model"

    def test_strips_whitespace_from_arg(self) -> None:
        """Extra whitespace around the arg is stripped."""
        _, arg = parse_slash_command("/remember   hello world   ")
        assert arg == "hello world"

    def test_preserves_spaces_in_arg(self) -> None:
        """Internal spaces in the arg are preserved."""
        _, arg = parse_slash_command("/remember remember that thing")
        assert arg == "remember that thing"

    def test_multiple_words_in_arg(self) -> None:
        """Only the first word is used as the command; rest is arg."""
        cmd, arg = parse_slash_command("/echo hello world foo")
        assert cmd == "echo"
        assert arg == "hello world foo"

    def test_unknown_command_returns_as_is(self) -> None:
        """Unknown commands are still parsed (caller decides if valid)."""
        cmd, arg = parse_slash_command("/nonexistent something")
        assert cmd == "nonexistent"
        assert arg == "something"

    def test_exit_command(self) -> None:
        """Exit/close variants are parsed correctly."""
        assert parse_slash_command("/exit") == ("exit", "")
        assert parse_slash_command("/close") == ("close", "")
        assert parse_slash_command("/quit") == ("quit", "")


class TestSlashCommandsList:
    """Tests for the SLASH_COMMANDS constant."""

    def test_all_have_cmd_and_desc(self) -> None:
        """Every entry has the required keys."""
        for entry in SLASH_COMMANDS:
            assert "cmd" in entry, f"Missing 'cmd' in {entry}"
            assert "desc" in entry, f"Missing 'desc' in {entry}"

    def test_cmds_start_with_slash(self) -> None:
        """All command names start with /."""
        for entry in SLASH_COMMANDS:
            assert entry["cmd"].startswith("/"), f"{entry['cmd']} doesn't start with /"

    def test_no_duplicate_commands(self) -> None:
        """No two entries have the same command name."""
        cmds = [entry["cmd"] for entry in SLASH_COMMANDS]
        assert len(cmds) == len(set(cmds)), f"Duplicate commands: {cmds}"

    def test_contains_expected_commands(self) -> None:
        """The standard set of commands is present."""
        expected = {"/model", "/settings", "/clear", "/status", "/help", "/exit"}
        actual = {entry["cmd"] for entry in SLASH_COMMANDS}
        assert expected.issubset(actual), f"Missing: {expected - actual}"
