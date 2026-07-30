"""The bind guard and the duration parser are two silent-failure surfaces.

`_validate_http_bind` is the only thing stopping ``enji-guard run
--transport streamable-http --host 0.0.0.0`` from publishing the MCP
server to the network, and `_parse_duration` decides whether ``--timeout
5m`` means five minutes or five seconds.  Both are cheap enough that the
CRAP gate passes them at zero coverage, so an inverted condition or a
wrong default multiplier would ship green.  Both are asserted here on
observable behaviour: the guard through the real ``run`` command, the
parser on the value it returns.
"""

import pytest
import typer
from typer.testing import CliRunner

from enji_guard_cli.delivery import service as service_module
from enji_guard_cli.delivery.cli.app import _parse_duration, app
from enji_guard_cli.runtime_observability.supervisor import RuntimeServiceOptions

HTTP_TRANSPORTS = ["sse", "streamable-http"]
LOOPBACK_HOSTS = ["127.0.0.1", "127.53.1.9", "::1", "localhost", "LOCALHOST", "  127.0.0.1  "]
EXTERNAL_HOSTS = ["0.0.0.0", "192.168.1.10", "::", "2001:db8::1", "example.com", "", "   "]

BIND_REFUSAL = "HTTP MCP transports may only bind to loopback by default"


@pytest.mark.parametrize("transport", HTTP_TRANSPORTS)
@pytest.mark.parametrize("host", LOOPBACK_HOSTS)
def test_http_transports_may_bind_to_loopback(host: str, transport: str) -> None:
    service_module._validate_http_bind(host, transport, allow_external_host=False)


@pytest.mark.parametrize("transport", HTTP_TRANSPORTS)
@pytest.mark.parametrize("host", EXTERNAL_HOSTS)
def test_http_transports_refuse_a_non_loopback_bind(host: str, transport: str) -> None:
    """A hostname is refused too: it cannot be proven loopback without DNS."""
    with pytest.raises(typer.BadParameter):
        service_module._validate_http_bind(host, transport, allow_external_host=False)


@pytest.mark.parametrize("host", EXTERNAL_HOSTS)
def test_stdio_never_binds_a_socket_so_the_host_is_irrelevant(host: str) -> None:
    service_module._validate_http_bind(host, "stdio", allow_external_host=False)


@pytest.mark.parametrize("transport", HTTP_TRANSPORTS)
@pytest.mark.parametrize("host", EXTERNAL_HOSTS)
def test_the_explicit_opt_in_allows_an_external_bind(host: str, transport: str) -> None:
    service_module._validate_http_bind(host, transport, allow_external_host=True)


def _service_run_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[RuntimeServiceOptions, bool]]:
    """Record the real CLI alias's call into the optional service boundary."""
    calls: list[tuple[RuntimeServiceOptions, bool]] = []

    def fake_service_run(
        options: RuntimeServiceOptions, *, allow_external_host: bool = False, auth_file: object = None
    ) -> None:
        del auth_file
        calls.append((options, allow_external_host))

    monkeypatch.setattr(service_module, "run", fake_service_run)
    return calls


def test_the_run_command_refuses_an_external_http_bind_before_starting() -> None:
    result = CliRunner().invoke(app, ["run", "--transport", "streamable-http", "--host", "0.0.0.0"])

    assert result.exit_code == 2
    assert BIND_REFUSAL in result.stderr


def test_the_run_command_starts_an_external_bind_once_it_is_opted_into(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _service_run_calls(monkeypatch)

    result = CliRunner().invoke(
        app,
        ["run", "--transport", "streamable-http", "--host", "0.0.0.0", "--allow-external-host"],
    )

    assert result.exit_code == 0, result.output
    assert [(options.transport, options.host, allow_external) for options, allow_external in calls] == [
        ("streamable-http", "0.0.0.0", True)
    ]


def test_the_run_command_starts_a_loopback_bind_without_any_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _service_run_calls(monkeypatch)

    result = CliRunner().invoke(app, ["run", "--transport", "streamable-http", "--host", "127.0.0.1"])

    assert result.exit_code == 0, result.output
    assert [(options.transport, options.host, allow_external) for options, allow_external in calls] == [
        ("streamable-http", "127.0.0.1", False)
    ]


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("45", 45),
        ("45s", 45),
        ("5m", 300),
        ("2h", 7200),
        ("3d", 259200),
        ("  5M  ", 300),
        ("0s", 0),
    ],
)
def test_a_duration_resolves_to_the_seconds_its_suffix_names(text: str, seconds: int) -> None:
    assert _parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "   ", "m", "s", "5x", "-5", "1.5h", "five", "5 m"])
def test_an_unparseable_duration_is_refused_rather_than_guessed(text: str) -> None:
    with pytest.raises(ValueError, match="duration"):
        _parse_duration(text)
