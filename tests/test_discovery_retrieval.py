"""Analyzer, frozen index artifacts, intent parsing, and hybrid retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest

from mandateguard.discovery.index.analyzer import (
    ANALYZER_VERSION,
    STOP_WORDS,
    analyze,
    analyze_unique,
    fold,
    normalize,
)
from mandateguard.discovery.index.artifacts import (
    ArtifactError,
    pack_string_table,
    pack_varints,
    read_artifact,
    unpack_string_table,
    unpack_varints,
    write_artifact,
)
from mandateguard.discovery.index.embedding import (
    load_embedding_index,
    quantize,
    write_embedding_index,
)
from mandateguard.discovery.index.hybrid import (
    DEFAULT_ALPHA,
    HybridDiscoveryRetriever,
    StructuredFilter,
)
from mandateguard.discovery.index.lexical import (
    FIELD_WEIGHTS,
    build_lexical_index,
    field_terms,
    load_lexical_index,
    write_lexical_index,
)
from mandateguard.discovery.intent import (
    AMBIGUOUS_MONETARY_CONSTRAINT,
    CONFLICTING_MONETARY_CONSTRAINT,
    INVALID_MONETARY_CONSTRAINT,
    MonetaryConstraintError,
    PARSER_VERSION,
    parse_intent,
    parse_monetary_constraint,
    reject_monetary_problem,
)

from tests.discovery_factories import build_catalog, build_product


# --------------------------------------------------------------------------
# Analyzer
# --------------------------------------------------------------------------


def test_the_analyzer_folds_plurals_so_lamps_matches_lamp() -> None:
    assert fold("lamps") == "lamp"
    assert fold("watches") == "watch"
    assert fold("batteries") == "battery"
    # A word that merely ends in s is not a plural.
    assert fold("glass") == "glass"


def test_accents_and_case_are_folded_before_tokenizing() -> None:
    assert normalize("Café") == "cafe"
    assert analyze("Café Lamps") == ["cafe", "lamp"]


def test_negation_words_survive_the_stop_list() -> None:
    """A mandate says "no subscriptions". Dropping "no" would invert it."""

    assert "no" not in STOP_WORDS
    assert "not" not in STOP_WORDS
    assert "no" in analyze("no subscriptions")


def test_analyze_unique_preserves_first_occurrence_order() -> None:
    assert analyze_unique("lamp desk lamp study") == ["lamp", "desk", "study"]


# --------------------------------------------------------------------------
# Artifact container
# --------------------------------------------------------------------------


def test_varints_round_trip_including_multibyte_values() -> None:
    values = [0, 1, 127, 128, 300, 16_384, 2_097_152]
    blob = pack_varints(values)
    decoded, cursor = unpack_varints(blob, 0, len(values))
    assert decoded == values
    assert cursor == len(blob)


def test_a_truncated_varint_stream_is_an_error_not_a_silent_zero() -> None:
    with pytest.raises(ArtifactError):
        unpack_varints(pack_varints([1, 2]), 0, 5)


def test_string_tables_round_trip_unicode() -> None:
    items = ["lamp", "kurtā", "बैग", "watch"]
    joined, offsets = pack_string_table(items)
    assert unpack_string_table(joined, offsets) == items


def test_an_artifact_round_trips_its_header_and_sections(tmp_path: Path) -> None:
    path = tmp_path / "artifact.mgdx"
    write_artifact(path, {"kind": "test", "n": 3}, {"a": b"one", "b": b"two"})
    artifact = read_artifact(path)
    assert artifact.require("kind") == "test"
    assert artifact.section("a") == b"one"
    assert artifact.section("b") == b"two"


def test_a_file_that_is_not_an_artifact_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "not-an-artifact.mgdx"
    path.write_bytes(b"just some bytes")
    with pytest.raises(ArtifactError):
        read_artifact(path)


def test_a_missing_artifact_section_is_named_in_the_error(tmp_path: Path) -> None:
    path = tmp_path / "artifact.mgdx"
    write_artifact(path, {"kind": "test"}, {"a": b"one"})
    with pytest.raises(ArtifactError) as error:
        read_artifact(path).section("missing")
    assert "missing" in str(error.value)


# --------------------------------------------------------------------------
# Lexical index
# --------------------------------------------------------------------------


def test_a_title_term_outweighs_the_same_term_in_the_description() -> None:
    assert FIELD_WEIGHTS["title"] > FIELD_WEIGHTS["description"]
    stream = field_terms(
        title="Lamp", brand=None, category="Lighting", description="lamp lamp"
    )
    assert stream.count("lamp") == FIELD_WEIGHTS["title"] + 2 * FIELD_WEIGHTS["description"]


def _write_indexes(tmp_path: Path, catalog) -> tuple[Path, Path]:
    streams = [
        field_terms(
            title=product.title,
            brand=product.brand,
            category=product.category_text,
            description=product.description,
        )
        for product in catalog
    ]
    lexical_path = tmp_path / "lexical.mgdx"
    write_lexical_index(
        build_lexical_index(streams), lexical_path, catalog_sha256=catalog.catalog_sha256
    )
    embedding_path = tmp_path / "embedding.mgdx"
    terms = sorted({term for stream in streams for term in stream})
    # A toy two-dimensional space: enough to exercise encode and similarity.
    rows = [[1.0 if index % 2 == 0 else 0.0, 0.5] for index in range(len(terms))]
    vectors = [[1.0, 0.0] for _ in catalog]
    write_embedding_index(
        embedding_path,
        dimensions=2,
        terms=terms,
        projection_rows=rows,
        document_vectors=vectors,
        catalog_sha256=catalog.catalog_sha256,
        explained_variance=0.4,
        trainer={"id": "test"},
    )
    return lexical_path, embedding_path


def test_the_lexical_index_round_trips_and_ranks(tmp_path: Path) -> None:
    catalog = build_catalog()
    lexical_path, _ = _write_indexes(tmp_path, catalog)
    index = load_lexical_index(lexical_path)
    assert index.document_count == len(catalog)
    ranked = index.score(analyze_unique("notebook"), limit=5)
    assert ranked
    best = ranked[0][0]
    assert catalog[best].title == "Field Notebook Set"


def test_an_index_built_by_another_analyzer_version_refuses_to_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = build_catalog()
    lexical_path, _ = _write_indexes(tmp_path, catalog)
    monkeypatch.setattr(
        "mandateguard.discovery.index.lexical.ANALYZER_VERSION", "different-version"
    )
    with pytest.raises(ArtifactError) as error:
        load_lexical_index(lexical_path)
    assert "analyzer version" in str(error.value)


def test_quantization_preserves_direction_within_int8_resolution() -> None:
    packed, scale = quantize([1.0, -0.5, 0.0])
    assert scale > 0
    assert packed[0] == 127
    assert packed[1] == (256 - 64)
    assert packed[2] == 0


def test_quantizing_a_zero_vector_reports_a_zero_scale_rather_than_dividing() -> None:
    packed, scale = quantize([0.0, 0.0])
    assert scale == 0.0
    assert packed == b"\x00\x00"


def test_encoding_a_query_with_no_known_term_returns_nothing(tmp_path: Path) -> None:
    catalog = build_catalog()
    _, embedding_path = _write_indexes(tmp_path, catalog)
    embedding = load_embedding_index(embedding_path)
    assert embedding.encode("zzzqqxx") is None
    assert embedding.encode("lamp") is not None


# --------------------------------------------------------------------------
# Intent parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_minor"),
    [
        ("Buy Sony headphones under Rs 5000", 500_000),
        ("desk lamp below 1500", 150_000),
        ("a laptop under 1.2 lakh", 12_000_000),
        ("headphones at most ₹4,000", 400_000),
        ("phone up to 25k", 2_500_000),
        ("a notebook with no budget stated", None),
    ],
)
def test_price_ceilings_are_extracted_by_rule(text: str, expected_minor: int | None) -> None:
    assert parse_intent(text).max_total_minor == expected_minor


def test_an_absent_ceiling_is_reported_rather_than_defaulted() -> None:
    parsed = parse_intent("buy a desk lamp")
    assert parsed.max_total_minor is None
    assert "PRICE_CEILING_ABSENT" in parsed.unresolved
    assert "No spending ceiling was stated" in " ".join(parsed.plain_english())


def test_a_bare_amount_is_not_promoted_to_a_ceiling() -> None:
    """A price with no comparator is an observation, not a budget.

    The previous parser read "headphones ₹4000" as a ₹4,000 ceiling and noted
    the inference. That is a guess about how much the user is willing to spend,
    made from a number that may just as easily have been a price they saw. The
    parser now refuses it and says which kind of refusal it is.
    """

    parsed = parse_intent("headphones ₹4000")
    assert parsed.max_total_minor is None
    assert parsed.parse_problems == (AMBIGUOUS_MONETARY_CONSTRAINT,)
    assert "PRICE_CEILING_INFERRED_FROM_BARE_AMOUNT" not in parsed.unresolved


def test_exclusions_and_recurrence_are_extracted_from_ordinary_sentences() -> None:
    parsed = parse_intent(
        "Get a desk lamp below Rs 1500 and no subscriptions or memberships"
    )
    assert parsed.recurring_allowed is False
    assert any("subscription" in item.casefold() for item in parsed.exclusions)


def test_an_unstated_recurrence_stance_is_never_assumed_permissive() -> None:
    parsed = parse_intent("Buy a desk lamp under Rs 1500")
    assert parsed.recurring_allowed is None
    assert "RECURRENCE_STANCE_ABSENT" in parsed.unresolved
    assert "must be evidenced, not assumed" in " ".join(parsed.plain_english())


def test_gambling_exclusion_survives_a_long_sentence() -> None:
    parsed = parse_intent(
        "Find an introductory finance course but nothing about gambling"
    )
    assert any("gambling" in item.casefold() for item in parsed.exclusions)


def test_quantity_is_extracted_and_divides_the_per_unit_ceiling() -> None:
    parsed = parse_intent("Buy 3 units of notebooks under Rs 900")
    assert parsed.quantity == 3
    assert parsed.max_total_minor == 90_000
    assert parsed.max_unit_price_minor == 30_000


def test_a_brand_the_catalog_carries_is_recognised_and_an_unknown_one_is_not() -> None:
    parsed = parse_intent(
        "Buy Sony headphones under Rs 5000", known_brands=["Sony", "Aurora"]
    )
    assert parsed.brand_hints == ("Sony",)
    assert parse_intent("Buy Zzyzx headphones", known_brands=["Sony"]).brand_hints == ()


def test_the_search_text_drops_constraint_clauses_and_keeps_the_product() -> None:
    parsed = parse_intent("Buy Sony headphones under Rs 5000. One-time payment only.")
    assert "sony" in parsed.search_text
    assert "headphone" in parsed.search_text
    assert "5000" not in parsed.search_text


def test_an_empty_or_oversized_intent_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_intent("   ")
    with pytest.raises(ValueError):
        parse_intent("x" * 5000)


def test_the_parser_version_travels_with_its_output() -> None:
    assert parse_intent("a lamp").to_mapping()["parser_version"] == PARSER_VERSION


# --------------------------------------------------------------------------
# Hybrid retrieval and structured filters
# --------------------------------------------------------------------------


def _retriever(tmp_path: Path, catalog) -> HybridDiscoveryRetriever:
    lexical_path, embedding_path = _write_indexes(tmp_path, catalog)
    return HybridDiscoveryRetriever(
        lexical=load_lexical_index(lexical_path),
        embedding=load_embedding_index(embedding_path),
        product_at=lambda document_id: catalog[document_id],
    )


def test_a_price_ceiling_removes_a_listing_from_candidacy_entirely(tmp_path: Path) -> None:
    catalog = build_catalog()
    retriever = _retriever(tmp_path, catalog)
    unfiltered = retriever.retrieve(query="lamp", top_k=5)
    assert {item.product.title for item in unfiltered.listings} >= {
        "StudyGlow Desk Lamp",
        "Aurora Focus Lamp",
    }
    filtered = retriever.retrieve(
        query="lamp",
        structured=StructuredFilter(max_unit_price_minor=130_000, currency="INR"),
        top_k=5,
    )
    titles = {item.product.title for item in filtered.listings}
    assert "Aurora Focus Lamp" not in titles
    assert filtered.filtered_out["ABOVE_PRICE_CEILING"] == 1


def test_a_listing_matching_an_exclusion_is_not_ranked_lower_but_removed(
    tmp_path: Path,
) -> None:
    catalog = build_catalog()
    retriever = _retriever(tmp_path, catalog)
    outcome = retriever.retrieve(
        query="lamp notebook",
        structured=StructuredFilter(exclusion_terms=("notebook",)),
        top_k=5,
    )
    assert all("Notebook" not in item.product.title for item in outcome.listings)
    assert outcome.filtered_out["MATCHES_MANDATE_EXCLUSION"] == 1


def test_a_listing_without_a_price_cannot_satisfy_a_budget(tmp_path: Path) -> None:
    catalog = build_catalog(
        (
            build_product(source_product_id="priced", price_minor=50_000),
            build_product(
                source_product_id="unpriced", title="Mystery Lamp", price_minor=None
            ),
        )
    )
    retriever = _retriever(tmp_path, catalog)
    outcome = retriever.retrieve(
        query="lamp",
        structured=StructuredFilter(max_unit_price_minor=100_000),
        top_k=5,
    )
    assert all(item.product.price_minor is not None for item in outcome.listings)
    assert outcome.filtered_out["PRICE_NOT_PUBLISHED"] == 1


def test_retrieval_rejects_an_out_of_range_blend_or_depth(tmp_path: Path) -> None:
    retriever = _retriever(tmp_path, build_catalog())
    with pytest.raises(ValueError):
        retriever.retrieve(query="lamp", alpha=1.5)
    with pytest.raises(ValueError):
        retriever.retrieve(query="lamp", top_k=0)
    with pytest.raises(ValueError):
        retriever.retrieve(query="lamp", top_k=5, candidate_depth=2)


def test_the_default_blend_is_the_one_the_frozen_evaluation_selected() -> None:
    """Changing this silently would change the shipped ranking."""

    assert DEFAULT_ALPHA == 1.0


def test_near_duplicate_listings_are_collapsed(tmp_path: Path) -> None:
    duplicate = build_product(source_product_id="dup", title="StudyGlow Desk Lamp")
    catalog = build_catalog(
        (build_product(source_product_id="orig"), duplicate, build_product(
            source_product_id="other", title="Aurora Focus Lamp"
        ))
    )
    retriever = _retriever(tmp_path, catalog)
    raw = retriever.retrieve(query="lamp", top_k=5, deduplicate=False)
    collapsed = retriever.retrieve(query="lamp", top_k=5, deduplicate=True)
    assert len(raw.listings) > len(collapsed.listings)
    titles = [item.product.title for item in collapsed.listings]
    assert len(titles) == len(set(titles))
    assert collapsed.duplicates_suppressed >= 1


def test_matched_terms_are_reported_so_a_match_can_be_explained(tmp_path: Path) -> None:
    retriever = _retriever(tmp_path, build_catalog())
    outcome = retriever.retrieve(query="desk lamp", top_k=1)
    assert outcome.listings
    assert "lamp" in outcome.listings[0].matched_terms


def test_retrieval_without_an_embedding_index_still_works(tmp_path: Path) -> None:
    catalog = build_catalog()
    lexical_path, _ = _write_indexes(tmp_path, catalog)
    retriever = HybridDiscoveryRetriever(
        lexical=load_lexical_index(lexical_path),
        embedding=None,
        product_at=lambda document_id: catalog[document_id],
    )
    outcome = retriever.retrieve(query="lamp", top_k=3)
    assert outcome.listings
    assert outcome.dense_available is False


# --------------------------------------------------------------------------
# Monetary constraints must fail closed
#
# A malformed money phrase has exactly one safe reading: refuse it. The failure
# mode this guards is specific and was live - "under -₹4000" losing its sign,
# becoming a ₹4,000 budget, and authorizing an order the user never described.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "under -₹4000",
        "under -Rs 4000",
        "under -4000",
        "₹-4000",
        "Rs -4000",
        "budget of -4000",
        "at most -₹ 4000",
    ],
)
def test_a_negative_amount_never_becomes_a_positive_budget(text: str) -> None:
    result = parse_monetary_constraint(text)
    assert result.problem == INVALID_MONETARY_CONSTRAINT
    assert result.max_total_minor is None
    # The specific regression: the magnitude must not survive the sign.
    assert result.max_total_minor != 400_000


@pytest.mark.parametrize("text", ["under ₹0", "under 0", "under Rs 0", "budget of 0"])
def test_a_zero_ceiling_is_rejected_rather_than_treated_as_absent(text: str) -> None:
    result = parse_monetary_constraint(text)
    assert result.problem == INVALID_MONETARY_CONSTRAINT
    assert result.max_total_minor is None


@pytest.mark.parametrize("text", ["under 4e3", "under 4E3", "under 1.2e4"])
def test_scientific_notation_is_refused_not_partially_matched(text: str) -> None:
    assert parse_monetary_constraint(text).problem == INVALID_MONETARY_CONSTRAINT


@pytest.mark.parametrize("text", ["under 4.000", "under Rs 4.000", "under 1.234"])
def test_ambiguous_dotted_thousands_are_refused(text: str) -> None:
    """"4.000" is four in one locale and four thousand in another."""

    assert parse_monetary_constraint(text).problem == AMBIGUOUS_MONETARY_CONSTRAINT


@pytest.mark.parametrize(
    "text",
    ["4000 INR", "headphones ₹4000", "Rs 4000", "₹4,000 headphones"],
)
def test_a_currency_amount_without_a_comparator_is_ambiguous(text: str) -> None:
    assert parse_monetary_constraint(text).problem == AMBIGUOUS_MONETARY_CONSTRAINT


@pytest.mark.parametrize(
    "text",
    [
        "under ₹4000 and ₹5000",
        "under 4000 and USD 50",
        "under Rs 4000 but no more than Rs 3000",
        "under $50 and ₹4000",
    ],
)
def test_two_money_clauses_are_conflicting_not_merged(text: str) -> None:
    result = parse_monetary_constraint(text)
    assert result.problem == CONFLICTING_MONETARY_CONSTRAINT
    assert result.max_total_minor is None


def test_an_amount_never_borrows_a_currency_from_another_clause() -> None:
    """The amount and the currency must come from the same phrase."""

    result = parse_monetary_constraint("under 4000 and USD 50")
    assert result.max_total_minor is None
    assert result.problem == CONFLICTING_MONETARY_CONSTRAINT


@pytest.mark.parametrize(
    ("text", "expected_minor", "expected_currency"),
    [
        ("from ₹2000 to ₹4000", 400_000, "INR"),
        ("₹4000 max", 400_000, "INR"),
        ("4,000 max", 400_000, "INR"),
        ("under Rs 4000", 400_000, "INR"),
        ("phone up to 25k", 2_500_000, "INR"),
        ("a laptop under 1.2 lakh", 12_000_000, "INR"),
        ("under $50", 5_000, "USD"),
    ],
)
def test_well_formed_constraints_still_parse(
    text: str, expected_minor: int, expected_currency: str
) -> None:
    result = parse_monetary_constraint(text)
    assert result.problem is None
    assert result.max_total_minor == expected_minor
    assert result.currency == expected_currency


def test_no_stated_budget_is_neither_a_problem_nor_a_ceiling() -> None:
    result = parse_monetary_constraint("buy a desk lamp, no subscriptions")
    assert result.problem is None
    assert result.max_total_minor is None


def test_a_rejected_constraint_stops_discovery_before_retrieval() -> None:
    """reject_monetary_problem is the choke point both halves call."""

    with pytest.raises(MonetaryConstraintError) as raised:
        reject_monetary_problem(parse_monetary_constraint("under -₹4000"))
    assert raised.value.code == INVALID_MONETARY_CONSTRAINT
    # The message a visitor sees names no internals and no filesystem path.
    assert "\\" not in raised.value.public_message
    assert "/" not in raised.value.public_message


def test_a_parsed_intent_carrying_a_money_problem_says_so_in_plain_english() -> None:
    parsed = parse_intent("Buy a lamp under -₹4000")
    assert parsed.parse_problems == (INVALID_MONETARY_CONSTRAINT,)
    assert parsed.max_total_minor is None
    spoken = " ".join(parsed.plain_english())
    assert "invalid or ambiguous" in spoken
    assert "authorization is blocked" in spoken
