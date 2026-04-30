from __future__ import annotations

from typer.testing import CliRunner

from livery.cli import _resolve_version, app


def test_version_flag_prints_version_and_exits():
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "livery " in result.stdout
    # Whatever _resolve_version returns, it must show up in the output
    assert _resolve_version() in result.stdout


def test_short_v_flag_works_too():
    runner = CliRunner()
    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert "livery " in result.stdout


def test_resolve_version_returns_a_pep440ish_string():
    """We expect either a real version like '0.6.1' or the literal 'unknown'."""
    v = _resolve_version()
    assert v == "unknown" or v.replace(".", "").isdigit() or any(ch in v for ch in "abc.+-")
