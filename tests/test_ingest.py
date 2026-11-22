import pytest

from clipkit.errors import ClipkitError
from clipkit.ingest import _download_command, assert_public_url, is_url


def test_is_url():
    assert is_url("https://example.com/video")
    assert not is_url("/tmp/video.mp4")


def test_private_urls_are_rejected():
    with pytest.raises(ClipkitError):
        assert_public_url("http://127.0.0.1/video")
    with pytest.raises(ClipkitError):
        assert_public_url("http://localhost/video")
    with pytest.raises(ClipkitError):
        assert_public_url("file:///etc/passwd")


def test_public_url_is_allowed():
    # A literal public IP avoids a DNS dependency in tests.
    assert_public_url("https://1.1.1.1/video")


def test_download_command_passes_cookies_proxy_and_section(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    cmd = _download_command(
        "https://example.com/video",
        "/tmp/video.%(ext)s",
        cookies=str(cookies),
        proxy="http://127.0.0.1:8080",
        section="*0-1200",
    )
    assert "--cookies" in cmd
    assert str(cookies) in cmd
    assert "--proxy" in cmd
    assert "http://127.0.0.1:8080" in cmd
    assert "--download-sections" in cmd
    assert "*0-1200" in cmd
    assert cmd[-2:] == ["/tmp/video.%(ext)s", "https://example.com/video"]


def test_download_command_rejects_missing_cookies(tmp_path):
    with pytest.raises(ClipkitError):
        _download_command(
            "https://example.com/video",
            "/tmp/video.%(ext)s",
            cookies=str(tmp_path / "missing.txt"),
        )
