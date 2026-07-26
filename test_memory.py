"""Plain-assert tests for app/memory.py. No pytest, no live API calls."""
import asyncio

from app import memory


def test_pair_key_order_independent_and_normalised():
    assert memory.pair_key(["bob", "alice"]) == memory.pair_key(["alice", "bob"])
    assert memory.pair_key(["alice", "bob"]) == "alice|bob"
    assert memory.pair_key(["alice", "", "bob"]) == "alice|bob"


def test_format_recall_empty():
    assert memory.format_recall([]) == ""


def test_format_recall_renders_rows():
    rows = [{"speaker_name": "Vatsa", "lang": "gu", "text": "x", "gloss": "rent is 20000",
             "term": "rent", "room_code": "R1", "said_at": None}]
    out = memory.format_recall(rows)
    assert "WHEN THIS CAME UP BEFORE" in out
    assert "Vatsa: rent is 20000" in out


def test_embed_returns_none_on_failure(monkeypatch):
    class _BoomClient:
        class embeddings:
            @staticmethod
            async def create(*a, **kw):
                raise RuntimeError("network unreachable")

    monkeypatch.setattr(memory, "_get_client", lambda: _BoomClient())
    result = asyncio.run(memory.embed("hello"))
    assert result is None


def test_recall_returns_empty_on_failure(monkeypatch):
    async def _fail_embed(text):
        return None

    monkeypatch.setattr(memory, "embed", _fail_embed)
    result = asyncio.run(memory.recall("a|b", "some gloss", "room1"))
    assert result == []


def test_remember_noop_on_embed_failure(monkeypatch):
    async def _fail_embed(text):
        return None

    calls = []

    async def _fake_get_pool():
        calls.append(1)
        raise AssertionError("get_pool should not be reached if embed fails")

    monkeypatch.setattr(memory, "embed", _fail_embed)
    monkeypatch.setattr(memory.db, "get_pool", _fake_get_pool)
    asyncio.run(memory.remember("room1", "a|b", "rent", "Vatsa", "gu", "text", "gloss"))
    assert calls == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            import inspect
            if "monkeypatch" in inspect.signature(t).parameters:
                import types
                class MP:
                    def setattr(self, obj, name, value):
                        setattr(obj, name, value)
                t(MP())
            else:
                t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            raise SystemExit(1)
    print("ALL GREEN")
