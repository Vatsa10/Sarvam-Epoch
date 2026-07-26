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


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\ntranslate sound\n")
