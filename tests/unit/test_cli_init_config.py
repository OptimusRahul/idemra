from pathlib import Path

from typer.testing import CliRunner

from idemra.cli.main import app

runner = CliRunner()


def test_init_then_config_end_to_end(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".idemra" / "permissions.yml").exists()

    result = runner.invoke(app, ["config", str(tmp_path)])
    assert result.exit_code == 0
    assert "approval_required" in result.stdout


def test_init_twice_does_not_clobber(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path)])
    permissions_path = tmp_path / ".idemra" / "permissions.yml"
    permissions_path.write_text("custom: true\n")

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0
    assert permissions_path.read_text() == "custom: true\n"


def test_config_without_init_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", str(tmp_path)])
    assert result.exit_code == 1
