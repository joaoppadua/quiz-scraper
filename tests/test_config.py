from bqpp.config import load_settings, load_taxonomy


def test_taxonomy_loads_all_spec_subtopics():
    tax = load_taxonomy()
    assert tax.discipline == "direito-processual-penal"
    # 4 (T1) + 9 (T2) + 5 (T3) + 3 (T4); the spec's "~20 subtopics" is an approximation
    assert len(tax.subtopic_ids) == 21
    assert {"T1.1", "T2.9", "T3.5", "T4.3"} <= tax.subtopic_ids
    assert tax.labels["T1.3"] == "Prisão temporária"


def test_validate_ids_rejects_unknown():
    tax = load_taxonomy()
    assert tax.validate_ids(["T1.1", "T9.9", "T2.4"]) == ["T9.9"]


def test_settings_defaults_and_llm_block():
    s = load_settings()
    assert s.llm.backend == "gemini"
    assert s.llm.fallback_backend == "openai"
    assert s.llm.max_attempts == 3
    assert s.db_path.name == "corpus.sqlite"


# ---- M3: subtopics that open from doctrine --------------------------------

def test_opens_with_defaults_to_none():
    from bqpp.config import load_taxonomy

    t = load_taxonomy()
    assert t.opens_with.get("T1.2") is None


def test_t33_is_marked_as_opening_from_doctrine():
    """~770 exam questions searched, zero standards-of-proof items. The professor
    opens this subtopic from doctrine, so it is not a coverage gap."""
    from bqpp.config import load_taxonomy

    assert load_taxonomy().opens_with.get("T3.3") == "doutrina"
