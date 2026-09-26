"""Phase 3.1 retrieval quality: classify, rerank, dedupe, grounded context."""
from __future__ import annotations

from careconnect_api.knowledge_rank import (
    classify_content,
    excerpt,
    prepare_grounded_context,
    query_intent,
    rank_results,
)


def test_classify_telemetry_key_values():
    text = "RH=46.1%, Bat=100%, T=32.4 C, RH=45.8%, Bat=99%"
    assert classify_content(text) == "telemetry"


def test_classify_narrative_and_table():
    assert (
        classify_content(
            "The adult brief sensor measures humidity and posts a silent alert for caregivers."
        )
        == "narrative"
    )
    table = "Metric | Value\nHumidity | 46%\nBattery | 100%\nAlert | Silent"
    assert classify_content(table, hint="table") == "table"


def test_query_intent_is_query_sensitive():
    expl = query_intent("How does the briefs sensor work?")
    assert expl["explanatory"] is True
    assert expl["telemetry"] is False
    telem = query_intent("What humidity readings were recorded during testing?")
    assert telem["telemetry"] is True


def _hit(**kwargs):
    base = {
        "chunkId": kwargs.get("chunkId", 1),
        "knowledgeBaseId": 1,
        "sourceId": 1,
        "sourceName": kwargs.get("sourceName", "Project-Public"),
        "topic": kwargs.get("topic"),
        "sectionTitle": kwargs.get("sectionTitle"),
        "text": kwargs.get("text", "x"),
        "score": kwargs.get("score", 0.58),
        "vectorScore": kwargs.get("score", 0.58),
        "contentKind": kwargs.get("contentKind", "narrative"),
        "slideNumber": kwargs.get("slideNumber"),
        "pageNumber": kwargs.get("pageNumber"),
        "revelTag": kwargs.get("revelTag"),
        "originalFilename": "deck.pptx",
    }
    return base


def test_explanatory_query_outranks_telemetry():
    query = "How does the briefs sensor work?"
    ranked = rank_results(
        query,
        [
            _hit(
                chunkId=1,
                score=0.60,
                contentKind="telemetry",
                sectionTitle="Lab log",
                text="RH=46.1% Bat=100% T=32.4 RH=45.2% Bat=99% T=32.1",
                topic="Adult Briefs Sensor",
            ),
            _hit(
                chunkId=2,
                score=0.57,
                contentKind="narrative",
                sectionTitle="Briefs Sensor",
                topic="Adult Briefs Sensor",
                text="The briefs sensor measures humidity in the brief and posts a silent alert.",
                slideNumber=2,
            ),
        ],
        min_score=0.35,
        limit=5,
    )
    assert ranked[0]["chunkId"] == 2
    assert ranked[0]["vectorScore"] == 0.57
    assert ranked[0]["score"] > ranked[0]["vectorScore"]
    assert "telemetry_penalty" in ranked[1]["rerankReasons"]


def test_telemetry_query_keeps_readings():
    query = "What humidity readings were recorded during testing?"
    ranked = rank_results(
        query,
        [
            _hit(
                chunkId=1,
                score=0.56,
                contentKind="narrative",
                sectionTitle="Briefs Sensor",
                text="The briefs sensor is a humidity monitor for adult briefs.",
            ),
            _hit(
                chunkId=2,
                score=0.55,
                contentKind="telemetry",
                sectionTitle="Test log",
                text="RH=46.1%, Bat=100%, T=32.4 during chamber testing.",
            ),
        ],
        min_score=0.35,
        limit=5,
    )
    assert ranked[0]["chunkId"] == 2
    assert "telemetry_query" in ranked[0]["rerankReasons"]


def test_benefits_and_care_connect_and_jstyle_characteristics():
    benefits = rank_results(
        "What are the benefits for care providers?",
        [
            _hit(
                chunkId=1,
                score=0.58,
                sourceName="Device Specs",
                sectionTitle="Radio",
                text="The radio uses 2.4 GHz BLE with a 3.7V pack.",
            ),
            _hit(
                chunkId=2,
                score=0.56,
                sourceName="Project-Public",
                sectionTitle="Care Provider Benefits",
                topic="Care Provider Benefits",
                text="Care providers get quieter nights and fewer wetness checks.",
            ),
        ],
    )
    assert benefits[0]["chunkId"] == 2

    overview = rank_results(
        "What is Care Connect?",
        [
            _hit(
                chunkId=1,
                score=0.59,
                sourceName="Briefs Sensor",
                sectionTitle="Briefs Sensor",
                text="The briefs sensor posts humidity alerts.",
            ),
            _hit(
                chunkId=2,
                score=0.56,
                sourceName="Care Connect Overview",
                sectionTitle="Care Connect",
                topic="Care Connect",
                text="Care Connect is a caregiver dashboard for Watcher companions.",
            ),
        ],
    )
    assert overview[0]["chunkId"] == 2

    watch = rank_results(
        "What does the J-Style watch measure?",
        [
            _hit(
                chunkId=1,
                score=0.58,
                sourceName="Project-Public",
                sectionTitle="Briefs Sensor",
                topic="Adult Briefs Sensor",
                text="The briefs sensor measures humidity in the brief.",
            ),
            _hit(
                chunkId=2,
                score=0.55,
                sourceName="J-Style Wearable",
                sectionTitle="J-Style watch",
                topic="J-Style",
                text="The J-Style watch measures heart rate and motion for the wearer.",
            ),
        ],
    )
    assert watch[0]["chunkId"] == 2


def test_dedupe_keeps_distinct_slides():
    query = "How does the briefs sensor work?"
    ranked = rank_results(
        query,
        [
            _hit(
                chunkId=1,
                score=0.61,
                slideNumber=2,
                sectionTitle="Briefs Sensor",
                text="The briefs sensor measures humidity and posts a silent alert.",
            ),
            _hit(
                chunkId=2,
                score=0.60,
                slideNumber=2,
                sectionTitle="Briefs Sensor",
                text="The briefs sensor measures humidity and posts a silent alert.",
            ),
            _hit(
                chunkId=3,
                score=0.58,
                slideNumber=5,
                sectionTitle="Installation",
                text="Place the sensor in the brief liner facing the skin.",
            ),
        ],
        limit=5,
    )
    ids = [row["chunkId"] for row in ranked]
    assert ids[0] == 1
    assert 2 not in ids
    assert 3 in ids


def test_min_score_does_not_drop_live_useful_band():
    ranked = rank_results(
        "How does the briefs sensor work?",
        [_hit(chunkId=1, score=0.55, sectionTitle="Briefs Sensor", text="The briefs sensor works by sampling humidity.")],
        min_score=0.35,
        limit=5,
    )
    assert len(ranked) == 1
    assert ranked[0]["vectorScore"] == 0.55


def test_excerpt_and_grounded_keep_full_text():
    text = "Intro. " + ("The briefs sensor measures humidity. " * 20)
    ranked = rank_results(
        "How does the briefs sensor work?",
        [_hit(chunkId=9, score=0.6, slideNumber=2, sourceName="Project-Public", topic="Adult Briefs Sensor", text=text, revelTag="bioev_humidity")],
    )
    assert ranked[0]["excerpt"]
    assert len(ranked[0]["excerpt"]) < len(text)
    grounded = prepare_grounded_context("How does the briefs sensor work?", ranked, knowledge_base_ids=[1])
    assert grounded["context"][0]["text"] == text
    assert grounded["context"][0]["citation"] == "Project-Public — Slide 2"
    assert grounded["context"][0]["chunkId"] == 9
    assert grounded["context"][0]["revelAutoTrigger"] is False
    assert grounded["knowledgeBases"] == [{"id": 1, "name": None}]
    assert excerpt(text, "briefs sensor").lower().find("briefs") >= 0
