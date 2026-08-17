"""Проверка ссылок и загрузок (§66, §68).

На своей машине ссылка — то, что человек сам вставил. На сервере это ввод
постороннего, и по нему сервер идёт куда скажут. Здесь проверяется, что
он туда не идёт.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narezka.core import registry, sources


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x.mp4",
        "http://127.0.0.1:8000/admin",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",  # ключи облачного провайдера
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://[::1]/",
        "https://user:pass@twitch.tv/videos/1",
    ],
)
def test_inside_and_tricky_urls_are_refused(url: str) -> None:
    with pytest.raises(sources.SourceError):
        sources.check(url)


def test_normal_link_passes() -> None:
    assert sources.check("https://www.twitch.tv/videos/123").startswith("https://")


def test_allowlist_matches_by_domain_not_by_string() -> None:
    """`notyoutube.com` не должен пройти как `youtube.com`: проверка идёт
    по домену с точкой, а не по концу строки."""
    allowed = ["youtube.com", "twitch.tv"]
    assert sources.check("https://m.youtube.com/watch?v=1", allowed_hosts=allowed)
    with pytest.raises(sources.SourceError):
        sources.check("https://notyoutube.com/watch?v=1", allowed_hosts=allowed)


def test_empty_allowlist_means_any_public_host() -> None:
    """На своей машине ограничивать человека в источниках незачем."""
    assert sources.check("https://example.com/video.mp4")


def test_registration_refuses_a_link_inside_the_network(tmp_path: Path) -> None:
    with pytest.raises(registry.RegistrationError, match="внутрь сети"):
        registry.register(
            storage_root=tmp_path, project="ivan", url="http://127.0.0.1/secret.mp4"
        )


def test_server_does_not_take_paths_when_asked_not_to(tmp_path: Path) -> None:
    """На сервере путь к файлу — чтение его же диска чужими руками."""
    media = tmp_path / "stream.mp4"
    media.write_bytes(b"\x00" * 2048)
    with pytest.raises(registry.RegistrationError, match="загрузить"):
        registry.register(
            storage_root=tmp_path, project="ivan", file=media, allow_local_paths=False
        )
