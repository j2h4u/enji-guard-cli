from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_default_command_is_loopback_safe() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'CMD ["run"]' in dockerfile
    assert '"--allow-external-host"' not in dockerfile
