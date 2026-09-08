from factlens.extractor import Evidence, Fact, _sentence_facts, relate

def fact(doc, value, period):
    return Fact(doc, f"Revenue was {value} in {period}.", "numeric", value, period, "revenue", .8, Evidence(doc, doc, 1, "sample"))

def test_same_period_different_values_are_flagged():
    assert relate([fact("a", "10%", "FY2024"), fact("b", "15%", "FY2024")])[0]["type"] == "contradicts"

def test_different_periods_are_reconciled():
    assert relate([fact("a", "10%", "FY2023"), fact("b", "10%", "FY2024")])[0]["type"] == "reconciles"


def test_footnote_marker_is_not_a_numeric_fact():
    facts = list(_sentence_facts("a", "a.pdf", 1, "41 World Bank outlook discussion.", 0))
    assert not any(fact.kind == "numeric" for fact in facts)


def test_different_periods_without_a_metric_do_not_reconcile():
    left = Fact("a", "Acme update in FY2023.", "semantic", None, "FY2023", "acme update", .8, Evidence("a", "a", 1, "Acme update in FY2023."))
    right = Fact("b", "Acme update in FY2024.", "semantic", None, "FY2024", "acme update", .8, Evidence("b", "b", 1, "Acme update in FY2024."))
    assert relate([left, right]) == []
