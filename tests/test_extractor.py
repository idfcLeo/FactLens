from factlens.extractor import Evidence, Fact, _sentence_facts, _normalized_number, relate

def fact(doc, value, period):
    claim = f"Revenue was {value} in {period}."
    return Fact(
        id=doc, claim=claim, kind="numeric", value=value, period=period,
        subject="revenue", confidence=0.8, evidence=Evidence(doc, doc, 1, claim),
        normalized_value=_normalized_number(value)
    )

def test_same_period_different_values_are_flagged():
    assert relate([fact("a", "10%", "FY2024"), fact("b", "15%", "FY2024")])[0]["type"] == "contradicts"

def test_different_periods_are_reconciled():
    assert relate([fact("a", "10%", "FY2023"), fact("b", "10%", "FY2024")])[0]["type"] == "reconciles"

def test_footnote_marker_is_not_a_numeric_fact():
    facts = list(_sentence_facts("a", "a.pdf", 1, "41 World Bank outlook discussion.", 0))
    assert not any(fact.kind == "numeric" for fact in facts)

def test_unrelated_sentences_without_metric_do_not_reconcile():
    left = Fact("a", "0.0 Dec-2014 Dec-2019", "semantic", None, "2014", "unclassified", .8, Evidence("a", "a", 1, "0.0 Dec-2014 Dec-2019"))
    right = Fact("b", "3 Dec 2021 Dec 2023", "semantic", None, "2021", "unclassified", .8, Evidence("b", "b", 1, "3 Dec 2021 Dec 2023"))
    assert relate([left, right]) == []
