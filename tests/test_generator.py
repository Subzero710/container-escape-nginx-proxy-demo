import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "create_project.py"
spec = importlib.util.spec_from_file_location("apex_generator", SCRIPT)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def run_generator(directory, *arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_generated_project_matches_repository(tmp_path):
    result = run_generator(tmp_path)
    assert result.returncode == 0, result.stderr
    actual = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()}
    assert actual == set(generator.FILES)
    assert not (ROOT / "backend/app.py").exists()
    for relative, content in generator.FILES.items():
        assert (tmp_path / relative).read_bytes() == content.encode("utf-8")
        assert (ROOT / relative).read_bytes() == content.encode("utf-8")
        assert relative in result.stdout
    assert (tmp_path / "certs/.gitkeep").read_bytes() == b""
    assert "docker compose up -d --build" in result.stdout
    assert "https://backend.apex.tld/items" in result.stdout


def test_existing_file_stops_all_writes(tmp_path):
    (tmp_path / "README.md").write_text("à conserver", encoding="utf-8")
    result = run_generator(tmp_path)
    assert result.returncode != 0
    assert "--force" in result.stderr
    assert {path.name for path in tmp_path.iterdir()} == {"README.md"}
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "à conserver"


def test_force_updates_project_and_preserves_certificates(tmp_path):
    assert run_generator(tmp_path).returncode == 0
    (tmp_path / "README.md").write_text("ancien README", encoding="utf-8")
    (tmp_path / "backend/app.py").write_text("ancien Flask", encoding="utf-8")
    (tmp_path / "certs/frontend.apex.tld.key").write_text("clé existante", encoding="utf-8")
    result = run_generator(tmp_path, "--force")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "backend/app.py").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == generator.FILES["README.md"]
    assert (tmp_path / "certs/frontend.apex.tld.key").read_text(encoding="utf-8") == "clé existante"


@pytest.mark.parametrize("relative", ["backend", "ckpit/app", "grafana/provisioning"])
def test_directory_collision_is_rejected_before_writing(tmp_path, relative):
    collision = tmp_path / relative
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_text("à conserver", encoding="utf-8")
    result = run_generator(tmp_path, "--force")
    assert result.returncode != 0
    assert not (tmp_path / "README.md").exists()
    assert collision.read_text(encoding="utf-8") == "à conserver"


def test_symlink_is_never_overwritten(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("à conserver", encoding="utf-8")
    (tmp_path / "README.md").symlink_to(outside)
    result = run_generator(tmp_path, "--force")
    assert result.returncode != 0
    assert outside.read_text(encoding="utf-8") == "à conserver"
    assert (tmp_path / "README.md").is_symlink()


def test_help_writes_nothing(tmp_path):
    assert run_generator(tmp_path, "--help").returncode == 0
    assert not list(tmp_path.iterdir())
