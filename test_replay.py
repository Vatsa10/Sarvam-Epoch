import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def test_all_scenarios_are_wellformed():
    files = sorted((ROOT / "fixtures").glob("scenario_*.json"))
    assert len(files) >= 3, "JTBD L4/L5 needs at least 3 repeated cases"
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        assert d["turns"] and d["expected"]
        for t in d["turns"]:
            assert t["party"] in {"vatsa", "sreedev"}
            assert t["transcript"].strip()


def test_scenarios_cover_the_three_scored_behaviours():
    states = set()
    for f in (ROOT / "fixtures").glob("scenario_*.json"):
        states |= set(json.loads(f.read_text(encoding="utf-8"))["expected"].values())
    assert {"AGREED", "DIVERGED", "HEDGED"} <= states


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nreplay fixtures sound\n")
