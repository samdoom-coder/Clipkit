import pytest

from clipkit.errors import ClipkitError
from clipkit.ingest import assert_public_url, is_url


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
