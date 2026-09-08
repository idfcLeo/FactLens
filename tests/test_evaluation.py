import json
from pathlib import Path
from factlens.extractor import Evidence, Fact, relate, _normalized_number

EVAL_FILE = Path(__file__).parent / "evaluation_dataset.json"


def load_eval_data():
    raw = json.loads(EVAL_FILE.read_text())
    cases = []
    for item in raw:
        left_dict = item["left"]
        right_dict = item["right"]
        
        left_ev = Evidence(**left_dict["evidence"])
        right_ev = Evidence(**right_dict["evidence"])

        left_fact = Fact(
            id=left_dict["id"],
            claim=left_dict["claim"],
            kind=left_dict["kind"],
            value=left_dict["value"],
            period=left_dict["period"],
            subject=left_dict["subject"],
            confidence=left_dict["confidence"],
            evidence=left_ev,
            normalized_value=_normalized_number(left_dict["value"])
        )

        right_fact = Fact(
            id=right_dict["id"],
            claim=right_dict["claim"],
            kind=right_dict["kind"],
            value=right_dict["value"],
            period=right_dict["period"],
            subject=right_dict["subject"],
            confidence=right_dict["confidence"],
            evidence=right_ev,
            normalized_value=_normalized_number(right_dict["value"])
        )

        cases.append((item["id"], left_fact, right_fact, item["expected_relation"]))
    return cases


def test_evaluation_benchmark():
    cases = load_eval_data()
    correct = 0
    total = len(cases)

    for case_id, left, right, expected in cases:
        relations = relate([left, right])
        actual_relation = relations[0]["type"] if relations else None
        
        assert actual_relation == expected, f"Case {case_id} failed: expected {expected}, got {actual_relation}"
        if actual_relation == expected:
            correct += 1

    accuracy = correct / total
    assert accuracy >= 0.85, f"Benchmark accuracy failed threshold: {accuracy:.1%}"


def test_unit_normalization():
    assert _normalized_number("$150 million") == 150_000_000.0
    assert _normalized_number("₹10 crore") == 100_000_000.0
    assert _normalized_number("5.5 lakh") == 550_000.0
    assert _normalized_number("18.5%") == 18.5
