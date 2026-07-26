# test_translate.py
import asyncio
import httpx
from app import sarvam


def test_translate_posts_expected_payload_and_returns_text():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"translated_text": "അതെ"})

    original = sarvam._client
    sarvam._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        out = asyncio.run(sarvam.translate("haan", "ml-IN", "gu-IN"))
    finally:
        asyncio.run(sarvam._client.aclose())
        sarvam._client = original

    assert out == "അതെ"
    assert captured["input"] == "haan"
    assert captured["target_language_code"] == "ml-IN"
    assert captured["source_language_code"] == "gu-IN"
    assert captured["model"] == "mayura:v1"
    assert captured["mode"] == "code-mixed"


def test_translate_returns_empty_string_on_missing_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    original = sarvam._client
    sarvam._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        out = asyncio.run(sarvam.translate("x", "ml-IN"))
    finally:
        asyncio.run(sarvam._client.aclose())
        sarvam._client = original
    assert out == ""



def test_same_language_is_a_noop_and_never_calls_the_api():
    """Mayura 400s when source == target, and that is reachable the moment either
    party picks English: both the gloss and the relay become en-IN -> en-IN."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(400, text="same language")

    original = sarvam._client
    sarvam._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        out = asyncio.run(sarvam.translate("hello there", "en-IN", "en-IN"))
    finally:
        asyncio.run(sarvam._client.aclose())
        sarvam._client = original

    assert out == "hello there"
    assert not called, "must short-circuit before hitting the API"


def test_error_body_is_surfaced_not_just_the_status():
    """A bare '400 Bad Request' mid-demo says nothing about which field was wrong."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid target_language_code: xx-XX")

    original = sarvam._client
    sarvam._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        asyncio.run(sarvam.translate("x", "xx-XX", "en-IN"))
    except httpx.HTTPStatusError as e:
        msg = str(e)
        assert "invalid target_language_code" in msg
        assert "en-IN->xx-XX" in msg
    else:
        raise AssertionError("should have raised")
    finally:
        asyncio.run(sarvam._client.aclose())
        sarvam._client = original


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\ntranslate sound\n")
