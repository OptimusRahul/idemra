from pathlib import Path

from idemra.config.scaffold import SCAFFOLD_FILES, idemra_dir, write_scaffold


def test_write_scaffold_creates_all_files(tmp_path: Path) -> None:
    written = write_scaffold(tmp_path)

    assert len(written) == len(SCAFFOLD_FILES)
    for name in SCAFFOLD_FILES:
        assert (idemra_dir(tmp_path) / name).exists()


def test_write_scaffold_is_idempotent(tmp_path: Path) -> None:
    write_scaffold(tmp_path)
    (idemra_dir(tmp_path) / "permissions.yml").write_text("custom: true\n")

    second = write_scaffold(tmp_path)

    assert second == []
    assert (idemra_dir(tmp_path) / "permissions.yml").read_text() == "custom: true\n"
