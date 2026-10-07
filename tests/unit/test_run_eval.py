from scripts.run_eval import BROAD, build


def test_build_adds_broad_questions_for_each_topic():
    docs = [
        {"id": "d2", "dept": "all", "topic": "expense-claims", "tier": "public"},
        {"id": "d1", "dept": "all", "topic": "expense-claims", "tier": "internal"},
        {"id": "d3", "dept": "all", "topic": "incident-response", "tier": "public"},
    ]

    questions = build(docs, {}, n_keys=0, n_probes=0)

    assert len(questions) == 2 * len(BROAD)
    assert [question["topic"] for question in questions] == [
        topic
        for topic in sorted({doc["topic"] for doc in docs})
        for _ in BROAD
    ]
    assert questions[0]["relevant"] == ["d1", "d2"]
