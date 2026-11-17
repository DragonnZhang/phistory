import hashlib
import io

import pytest

from phistory.interpreter import compatibility_metadata


def test_compatibility_header_comes_from_exact_tag_and_is_cached(tmp_path, monkeypatch):
    source = b'pub const OPEN_INTERPRETER_CODEX_COMPATIBILITY_VERSION: &str = "0.159.0";'
    requests = []

    def open_url(request, **kwargs):
        requests.append(request.full_url)
        return io.BytesIO(source)

    monkeypatch.setattr("phistory.interpreter.urllib.request.urlopen", open_url)
    result = compatibility_metadata(tmp_path, "rust-v0.0.61")
    assert result["release"] == "rust-v0.0.61"
    assert result["compatibility_version"] == "0.159.0"
    assert result["sha256"] == hashlib.sha256(source).hexdigest()
    assert "/rust-v0.0.61/codex-rs/product-info/src/lib.rs" in result["source"]
    assert compatibility_metadata(tmp_path, "rust-v0.0.61") == result
    assert len(requests) == 1
    compatibility_metadata(tmp_path, "rust-v0.0.62")
    assert len(requests) == 2


def test_missing_compatibility_header_fails_instead_of_using_product_version(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "phistory.interpreter.urllib.request.urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b'pub const VERSION: &str = "0.0.61";'),
    )
    with pytest.raises(RuntimeError, match="cannot derive"):
        compatibility_metadata(tmp_path, "rust-v0.0.61")
    assert not (tmp_path / "interpreter-compatibility.json").exists()
