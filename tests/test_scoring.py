from closeops.model import Finding
from closeops.scoring import score


def F(check, category, ids, rtype="invoice"):
    return Finding(check=check, category=category, severity="low", entity="E1", record_type=rtype,
                   record_ids=ids, evidence={}, confidence=0.9, suggested_action="")


KEY = [{"category": "duplicate", "check": "dup_exact", "record_id": "A"},
       {"category": "tax", "check": "tax_math", "record_id": "B"}]


def test_perfect():
    r = score([F("dup_exact", "duplicate", ["A", "X"]), F("tax_math", "tax", ["B"])], KEY)
    assert r["overall"] == {"tp": 2, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert r["by_category"]["tax"]["tp"] == 1 and r["by_check"]["dup_exact"]["tp"] == 1


def test_one_fp():
    r = score([F("dup_exact", "duplicate", ["A"]), F("tax_math", "tax", ["B"]),
               F("tax_math", "tax", ["Z"])], KEY)
    o = r["overall"]
    assert (o["tp"], o["fp"], o["fn"]) == (2, 1, 0)
    assert o["precision"] == 2 / 3 and o["recall"] == 1.0
    assert r["by_check"]["tax_math"]["fp"] == 1


def test_one_fn_and_soft_excluded_and_dict_input():
    soft = F("round_amount", "fraud", ["Q"]).to_dict()
    r = score([F("dup_exact", "duplicate", ["A"]), soft], KEY)
    o = r["overall"]
    assert (o["tp"], o["fp"], o["fn"]) == (1, 0, 1)
    assert r["by_check"]["tax_math"]["fn"] == 1
    assert "round_amount" not in r["by_check"]


def test_category_must_match_and_pair_ids_sorted():
    key = [{"category": "duplicate", "check": "dup_cross_entity", "record_id": "I1|I2"}]
    assert score([F("dup_cross_entity", "duplicate", ["I2", "I1"], "entity_pair")], key)["overall"]["tp"] == 1
    assert score([F("dup_cross_entity", "tax", ["I1|I2"])], key)["overall"]["tp"] == 0


def test_empty_is_zero_not_error():
    assert score([], [])["overall"]["f1"] == 0.0
