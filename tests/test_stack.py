from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = "nginx-proxy/conf.d/ckpit.apex.tld.conf"
STATIC_MOUNT = f"./{CONFIG_PATH}:/etc/nginx/conf.d/ckpit.apex.tld.conf:ro"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_expected_docker_services_and_single_public_proxy(compose):
    services = compose["services"]
    assert set(services) == {
        "nginx-proxy", "frontend", "backend", "postgres", "redis", "worker",
        "n8n", "n8n-postgres", "prometheus", "grafana",
    }
    assert "ckpit" not in services
    assert services["nginx-proxy"]["ports"] == ["80:80", "443:443"]
    for name, service in services.items():
        assert service["networks"] == ["app_net"]
        if name != "nginx-proxy":
            assert "ports" not in service


def test_proxy_can_reach_the_host_and_mounts_static_configuration(compose):
    proxy = compose["services"]["nginx-proxy"]
    assert proxy["image"] == "nginxproxy/nginx-proxy:latest"
    assert proxy["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert proxy["volumes"] == [
        "/var/run/docker.sock:/tmp/docker.sock:ro",
        "./certs:/etc/nginx/certs:ro",
        STATIC_MOUNT,
    ]
    assert (ROOT / CONFIG_PATH).is_file()


def test_public_container_hosts_remain_automatic(compose):
    expected = {"frontend": "80", "backend": "8000", "grafana": "3000", "n8n": "5678"}
    for name, port in expected.items():
        service = compose["services"][name]
        assert service["environment"]["VIRTUAL_HOST"] == f"{name}.apex.tld"
        assert service["environment"]["VIRTUAL_PORT"] == port
        assert service["environment"]["VIRTUAL_PROTO"] == "http"
        assert port in service["expose"]
    for name in {"postgres", "redis", "worker", "n8n-postgres", "prometheus"}:
        assert "VIRTUAL_HOST" not in compose["services"][name].get("environment", {})
    assert not any(
        service.get("environment", {}).get("VIRTUAL_HOST") == "ckpit.apex.tld"
        for service in compose["services"].values()
    )


def test_system_cockpit_has_no_application_directory():
    assert not (ROOT / "ckpit").exists()


def test_static_cockpit_route_preserves_tls_and_websocket_headers():
    config = (ROOT / CONFIG_PATH).read_text(encoding="utf-8")
    assert config.count("server_name ckpit.apex.tld;") == 2
    for directive in (
        "listen 80;", "listen [::]:80;",
        "return 301 https://$host$request_uri;",
        "listen 443 ssl;", "listen [::]:443 ssl;", "http2 on;",
        "ssl_certificate /etc/nginx/certs/ckpit.apex.tld.crt;",
        "ssl_certificate_key /etc/nginx/certs/ckpit.apex.tld.key;",
        "proxy_pass https://host.docker.internal:9090;",
        "proxy_http_version 1.1;", "proxy_buffering off;",
        "proxy_set_header Host $host;",
        "proxy_set_header X-Forwarded-Host $host;",
        "proxy_set_header X-Forwarded-Proto https;",
        "proxy_set_header X-Forwarded-Port 443;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header Upgrade $http_upgrade;",
        'proxy_set_header Connection "upgrade";',
        "proxy_ssl_verify off;",
    ):
        assert directive in config


def test_readme_documents_native_installation_and_origins():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in (
        "## Cockpit système hôte", "sudo apt update",
        "sudo apt install -y cockpit",
        "sudo systemctl enable --now cockpit.socket",
        "/etc/cockpit/cockpit.conf",
        "Origins = https://ckpit.apex.tld wss://ckpit.apex.tld",
        "ProtocolHeader = X-Forwarded-Proto",
        "sudo systemctl restart cockpit.socket",
        "sudo systemctl status cockpit.socket",
        "curl -k https://127.0.0.1:9090",
        "https://host.docker.internal:9090",
    ):
        assert command in readme
    assert "dashboard" not in readme.casefold()
    for host in ("frontend", "backend", "grafana", "n8n", "ckpit"):
        assert f"{host}.apex.tld.crt" in readme
        assert f"{host}.apex.tld.key" in readme
    assert "openssl req -x509 -newkey rsa:4096" in readme
    assert "for service in frontend backend grafana n8n ckpit; do" in readme


def test_application_data_services_are_preserved(compose):
    services = compose["services"]
    assert services["postgres"]["environment"]["POSTGRES_DB"] == "app"
    assert services["backend"]["environment"]["DATABASE_URL"] == "postgresql://app:app@postgres:5432/app"
    assert services["backend"]["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert services["n8n"]["environment"]["DB_POSTGRESDB_HOST"] == "n8n-postgres"
    assert set(compose["volumes"]) == {
        "postgres_data", "redis_data", "n8n_data", "n8n_postgres_data", "grafana_data",
    }
    assert services["prometheus"]["volumes"] == [
        "./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro"
    ]
