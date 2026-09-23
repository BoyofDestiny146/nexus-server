"""Orin boot-order drop-ins and web-static volume ownership."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JETSON = ROOT / "deploy/jetson"
COMPOSE = ROOT / "deploy/docker-compose.yml"
WEB_DOCKERFILE = ROOT / "web/Dockerfile"
WEB_ENTRYPOINT = ROOT / "web/docker-entrypoint.sh"


def test_containerd_dropin_waits_for_mnt_xiaozhi():
    text = (JETSON / "systemd/containerd.service.d/wait-mnt-xiaozhi.conf").read_text()
    assert "RequiresMountsFor=/mnt/xiaozhi" in text
    assert "After=mnt-xiaozhi.mount" in text
    assert "[Service]" not in text


def test_docker_dropin_waits_for_mnt_xiaozhi_and_containerd():
    text = (JETSON / "systemd/docker.service.d/wait-mnt-xiaozhi.conf").read_text()
    assert "RequiresMountsFor=/mnt/xiaozhi" in text
    assert "After=mnt-xiaozhi.mount containerd.service" in text
    assert "data-root" not in text.lower() or "must stay" in text.lower()
    assert "[Service]" not in text


def test_install_script_does_not_prune_or_move_docker_data():
    text = (JETSON / "install-docker-mount-order.sh").read_text()
    assert "RequiresMountsFor=/mnt/xiaozhi" in text
    assert "daemon-reload" in text
    assert "docker system prune" not in text
    assert "data-root" in text
    assert "--restart" in text
    assert "DO_RESTART=0" in text


def test_web_image_starts_as_root_and_chowns_volume():
    df = WEB_DOCKERFILE.read_text()
    assert "\nUSER appuser" not in df
    assert 'ENTRYPOINT ["/docker-entrypoint.sh"]' in df
    ep = WEB_ENTRYPOINT.read_text()
    assert "chown appuser:appuser /srv/web" in ep
    assert "chmod 0777" not in ep
    assert "chmod 777 /" not in ep


def test_compose_web_copy_runs_as_root_then_chowns_appuser():
    text = COMPOSE.read_text()
    web = text.split("  web:", 1)[1].split("  xiaozhi-server:", 1)[0]
    assert 'user: "0:0"' in web
    assert "chown appuser:appuser /srv/web" in web
    assert "chown -R appuser:appuser /srv/web" in web
    assert "chmod 0777" not in web
    assert "chmod 777 /" not in web
    assert "restart: \"no\"" in web


def test_compose_does_not_change_docker_root_dir():
    text = COMPOSE.read_text()
    assert "docker-data" in text
    assert "/mnt/xiaozhi/docker-data" in text
