"""Every way a frozen discovery artifact can be wrong, and the refusal for it.

The container is read at startup from files that ship inside a public image, so
its parser is the boundary between "a corrupt file" and "confident answers about
the wrong products". There is no warn-and-continue path here: a document id in a
stale index addresses a different listing than the same id in the loaded catalog,
so a mismatched pair does not degrade gracefully, it lies quietly.

Each test below corrupts exactly one thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandateguard.discovery.classifier import load_classifier, write_classifier
from mandateguard.discovery.index.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    MAGIC,
    Artifact,
    ArtifactError,
    read_artifact,
    unpack_string_table,
    validate_catalog_binding,
    write_artifact,
)
from mandateguard.discovery.index.embedding import load_embedding_index
from mandateguard.discovery.index.lexical import load_lexical_index


CATALOG_DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


# --------------------------------------------------------------------------
# Helpers: build a valid artifact, then bend one field of it
# --------------------------------------------------------------------------


def _write_valid(path: Path) -> None:
    write_artifact(
        path,
        {
            "kind": "test-kind",
            "catalog_sha256": CATALOG_DIGEST,
            "document_count": 3,
        },
        {"alpha": b"0123456789", "beta": b"abcdef"},
    )


def _split(payload: bytes) -> tuple[dict, bytes]:
    cursor = len(MAGIC)
    length = int.from_bytes(payload[cursor : cursor + 4], "big")
    cursor += 4
    header = json.loads(payload[cursor : cursor + length].decode("utf-8"))
    return header, payload[cursor + length :]


def _reassemble(header: dict, body: bytes) -> bytes:
    encoded = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return MAGIC + len(encoded).to_bytes(4, "big") + encoded + body


def _rewrite(path: Path, mutate) -> Path:
    header, body = _split(path.read_bytes())
    header, body = mutate(header, body)
    path.write_bytes(_reassemble(header, body))
    return path


# --------------------------------------------------------------------------
# Container-level corruption
# --------------------------------------------------------------------------


def test_a_valid_artifact_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "valid.mgdx"
    _write_valid(path)
    artifact = read_artifact(path)
    assert artifact.section("alpha") == b"0123456789"
    assert artifact.require("catalog_sha256") == CATALOG_DIGEST
    assert artifact.header["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_a_file_that_is_not_an_artifact_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "not-an-artifact.mgdx"
    path.write_bytes(b"PK\x03\x04 this is a zip file")
    with pytest.raises(ArtifactError, match="not a MandateGuard index artifact"):
        read_artifact(path)


def test_an_unsupported_schema_version_is_refused(tmp_path: Path) -> None:
    """An artifact from a previous container revision does not load.

    The v1 container had no length checks, no overlap checks, and no catalog
    binding. Accepting one now would accept everything it failed to police.
    """

    path = tmp_path / "old.mgdx"
    _write_valid(path)
    _rewrite(
        path,
        lambda header, body: (
            {**header, "artifact_schema_version": "mgdx-container-v1"},
            body,
        ),
    )
    with pytest.raises(ArtifactError, match="schema version is unsupported"):
        read_artifact(path)


def test_an_artifact_with_no_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "unversioned.mgdx"
    _write_valid(path)

    def drop(header, body):
        header.pop("artifact_schema_version")
        return header, body

    _rewrite(path, drop)
    with pytest.raises(ArtifactError, match="schema version is unsupported"):
        read_artifact(path)


def test_a_negative_section_length_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "negative.mgdx"
    _write_valid(path)

    def bend(header, body):
        header["sections"][0]["length"] = -4
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="negative"):
        read_artifact(path)


def test_a_negative_section_offset_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "negative-offset.mgdx"
    _write_valid(path)

    def bend(header, body):
        header["sections"][1]["offset"] = -1
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="negative"):
        read_artifact(path)


def test_a_duplicate_section_name_is_refused(tmp_path: Path) -> None:
    """Two entries for one name: the second silently wins, so neither is used."""

    path = tmp_path / "duplicate.mgdx"
    _write_valid(path)

    def bend(header, body):
        first = dict(header["sections"][0])
        first["name"] = header["sections"][1]["name"]
        header["sections"][0] = first
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="duplicate section name"):
        read_artifact(path)


def test_overlapping_sections_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "overlap.mgdx"
    _write_valid(path)

    def bend(header, body):
        # beta now starts inside alpha.
        header["sections"][1]["offset"] = 4
        header["sections"][1]["length"] = 6
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="overlap|unreferenced"):
        read_artifact(path)


def test_a_gap_between_sections_is_refused(tmp_path: Path) -> None:
    """Unreferenced bytes are somewhere to hide a payload."""

    path = tmp_path / "gap.mgdx"
    _write_valid(path)

    def bend(header, body):
        # Shift beta forward and pad the body so its declared span still fits.
        # Two bytes are now inside the file and named by no section.
        header["sections"][1]["offset"] = 12
        return header, body[:10] + b"\x00\x00" + body[10:]

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="unreferenced bytes"):
        read_artifact(path)


def test_a_section_outside_the_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "out-of-bounds.mgdx"
    _write_valid(path)

    def bend(header, body):
        header["sections"][1]["length"] = 4096
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="out of bounds"):
        read_artifact(path)


def test_an_impossibly_large_declared_section_is_refused(tmp_path: Path) -> None:
    """Refused on the declaration, before anything tries to allocate it."""

    path = tmp_path / "huge.mgdx"
    _write_valid(path)

    def bend(header, body):
        header["sections"][0]["length"] = 1 << 40
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="impossibly large"):
        read_artifact(path)


def test_a_truncated_payload_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "truncated.mgdx"
    _write_valid(path)
    payload = path.read_bytes()
    path.write_bytes(payload[: len(payload) - 5])
    with pytest.raises(ArtifactError, match="out of bounds"):
        read_artifact(path)


def test_a_truncated_header_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "short-header.mgdx"
    _write_valid(path)
    payload = path.read_bytes()
    # Keep the magic and the length prefix, drop most of the header.
    path.write_bytes(payload[: len(MAGIC) + 4 + 3])
    with pytest.raises(ArtifactError, match="truncated|not valid JSON"):
        read_artifact(path)


def test_a_file_with_only_the_magic_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "magic-only.mgdx"
    path.write_bytes(MAGIC)
    with pytest.raises(ArtifactError, match="header length is truncated"):
        read_artifact(path)


def test_unexpected_trailing_bytes_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "trailing.mgdx"
    _write_valid(path)
    path.write_bytes(path.read_bytes() + b"appended payload")
    with pytest.raises(ArtifactError, match="trailing bytes"):
        read_artifact(path)


def test_a_duplicate_json_key_in_the_header_is_refused(tmp_path: Path) -> None:
    """Two `catalog_sha256` fields: a reader picks one, an attacker picks which."""

    path = tmp_path / "dupe-key.mgdx"
    _write_valid(path)
    header, body = _split(path.read_bytes())
    raw = json.dumps(header, separators=(",", ":"))
    injected = raw[:-1] + f',"catalog_sha256":"{OTHER_DIGEST}"' + "}"
    encoded = injected.encode("utf-8")
    path.write_bytes(MAGIC + len(encoded).to_bytes(4, "big") + encoded + body)
    with pytest.raises(ArtifactError, match="not valid JSON"):
        read_artifact(path)


def test_a_header_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    encoded = json.dumps([1, 2, 3]).encode("utf-8")
    path = tmp_path / "array-header.mgdx"
    path.write_bytes(MAGIC + len(encoded).to_bytes(4, "big") + encoded)
    with pytest.raises(ArtifactError, match="must be a JSON object"):
        read_artifact(path)


def test_an_absurd_declared_header_length_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad-length.mgdx"
    path.write_bytes(MAGIC + (1 << 30).to_bytes(4, "big") + b"{}")
    with pytest.raises(ArtifactError, match="header length is invalid"):
        read_artifact(path)


def test_a_section_entry_with_extra_fields_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "extra-field.mgdx"
    _write_valid(path)

    def bend(header, body):
        header["sections"][0]["surprise"] = "value"
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="section table is malformed"):
        read_artifact(path)


def test_a_boolean_offset_is_not_accepted_as_an_integer(tmp_path: Path) -> None:
    """`True` is an int in Python. It is not an offset."""

    path = tmp_path / "bool-offset.mgdx"
    _write_valid(path)

    def bend(header, body):
        header["sections"][0]["offset"] = True
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="section table is malformed"):
        read_artifact(path)


def test_a_section_name_with_path_characters_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "path-name.mgdx"
    _write_valid(path)

    def bend(header, body):
        header["sections"][0]["name"] = "../escape"
        return header, body

    _rewrite(path, bend)
    with pytest.raises(ArtifactError, match="section name is invalid"):
        read_artifact(path)


def test_the_writer_refuses_to_forge_container_fields(tmp_path: Path) -> None:
    """A caller cannot smuggle its own section table into the header."""

    with pytest.raises(ArtifactError, match="reserved"):
        write_artifact(
            tmp_path / "forged.mgdx",
            {"kind": "test-kind", "sections": [{"name": "x", "offset": 0, "length": 0}]},
            {"alpha": b"1234"},
        )


def test_the_writer_refuses_an_invalid_section_name(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="section name is invalid"):
        write_artifact(tmp_path / "bad.mgdx", {"kind": "k"}, {"a/b": b"1234"})


# --------------------------------------------------------------------------
# Catalog binding
# --------------------------------------------------------------------------


def _artifact(**header) -> Artifact:
    return Artifact(header=header, sections={})


def test_a_matching_catalog_binding_is_accepted() -> None:
    digest, count = validate_catalog_binding(
        _artifact(catalog_sha256=CATALOG_DIGEST, document_count=17_702),
        expected_catalog_sha256=CATALOG_DIGEST,
        expected_document_count=17_702,
    )
    assert digest == CATALOG_DIGEST
    assert count == 17_702


def test_a_stale_catalog_digest_is_refused_not_warned_about() -> None:
    with pytest.raises(ArtifactError, match="digest does not match"):
        validate_catalog_binding(
            _artifact(catalog_sha256=OTHER_DIGEST, document_count=17_702),
            expected_catalog_sha256=CATALOG_DIGEST,
            expected_document_count=17_702,
        )


def test_a_document_count_that_disagrees_is_refused() -> None:
    with pytest.raises(ArtifactError, match="document count does not match"):
        validate_catalog_binding(
            _artifact(catalog_sha256=CATALOG_DIGEST, document_count=17_701),
            expected_catalog_sha256=CATALOG_DIGEST,
            expected_document_count=17_702,
        )


@pytest.mark.parametrize(
    "digest",
    ["", "not-a-digest", "A" * 64, "a" * 63, "a" * 65, 12345, None],
)
def test_a_malformed_catalog_digest_is_refused(digest: object) -> None:
    with pytest.raises(ArtifactError, match="catalog digest is invalid"):
        validate_catalog_binding(_artifact(catalog_sha256=digest, document_count=3))


@pytest.mark.parametrize("count", [0, -1, True, "3", None, 1.5])
def test_a_malformed_document_count_is_refused(count: object) -> None:
    with pytest.raises(ArtifactError, match="document count is invalid"):
        validate_catalog_binding(
            _artifact(catalog_sha256=CATALOG_DIGEST, document_count=count)
        )


def test_an_artifact_with_no_catalog_binding_at_all_is_refused() -> None:
    with pytest.raises(ArtifactError, match="'catalog_sha256' is missing"):
        validate_catalog_binding(_artifact(document_count=3))
    with pytest.raises(ArtifactError, match="'document_count' is missing"):
        validate_catalog_binding(_artifact(catalog_sha256=CATALOG_DIGEST))


# --------------------------------------------------------------------------
# String tables
# --------------------------------------------------------------------------


def test_a_string_table_offset_past_the_end_is_refused() -> None:
    joined = b"alphabeta"
    offsets = b"".join(value.to_bytes(4, "big") for value in (0, 5, 999))
    with pytest.raises(ArtifactError, match="out of bounds"):
        unpack_string_table(joined, offsets)


def test_a_string_table_that_does_not_start_at_zero_is_refused() -> None:
    offsets = b"".join(value.to_bytes(4, "big") for value in (2, 5, 9))
    with pytest.raises(ArtifactError, match="begin at offset zero"):
        unpack_string_table(b"alphabeta", offsets)


def test_a_string_table_with_trailing_bytes_is_refused() -> None:
    offsets = b"".join(value.to_bytes(4, "big") for value in (0, 5))
    with pytest.raises(ArtifactError, match="trailing bytes"):
        unpack_string_table(b"alphabeta", offsets)


def test_a_string_table_with_a_ragged_offset_block_is_refused() -> None:
    with pytest.raises(ArtifactError, match="offsets are malformed"):
        unpack_string_table(b"alpha", b"\x00\x00\x00")


def test_a_string_table_with_invalid_utf8_is_refused() -> None:
    joined = b"\xff\xfe"
    offsets = b"".join(value.to_bytes(4, "big") for value in (0, 2))
    with pytest.raises(ArtifactError, match="invalid UTF-8"):
        unpack_string_table(joined, offsets)


# --------------------------------------------------------------------------
# Typed loaders refuse the wrong kind and the wrong catalog
# --------------------------------------------------------------------------


def _valid_classifier(path: Path, *, catalog_sha256: str, document_count: int) -> None:
    write_classifier(
        path,
        model_id="test-classifier-v1",
        classes=("Clothing", "Lighting"),
        terms=["kurta", "lamp"],
        idf=[1.0, 1.0],
        coefficients=[[3.0, -3.0], [-3.0, 3.0]],
        intercepts=[0.0, 0.0],
        catalog_sha256=catalog_sha256,
        document_count=document_count,
        metrics={"test": {"macro_f1": 0.9}},
        trainer={"features": "title + description"},
    )


def test_a_classifier_bound_to_another_catalog_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "classifier.mgdx"
    _valid_classifier(path, catalog_sha256=OTHER_DIGEST, document_count=3)
    with pytest.raises(ArtifactError, match="digest does not match"):
        load_classifier(
            path, expected_catalog_sha256=CATALOG_DIGEST, expected_document_count=3
        )


def test_a_classifier_with_the_wrong_document_count_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "classifier.mgdx"
    _valid_classifier(path, catalog_sha256=CATALOG_DIGEST, document_count=3)
    with pytest.raises(ArtifactError, match="document count does not match"):
        load_classifier(
            path, expected_catalog_sha256=CATALOG_DIGEST, expected_document_count=4
        )


def test_a_matching_classifier_loads(tmp_path: Path) -> None:
    path = tmp_path / "classifier.mgdx"
    _valid_classifier(path, catalog_sha256=CATALOG_DIGEST, document_count=3)
    model = load_classifier(
        path, expected_catalog_sha256=CATALOG_DIGEST, expected_document_count=3
    )
    assert model.catalog_sha256 == CATALOG_DIGEST
    assert model.document_count == 3


def test_a_classifier_read_as_a_lexical_index_is_refused(tmp_path: Path) -> None:
    """Right container, wrong artifact kind."""

    path = tmp_path / "classifier.mgdx"
    _valid_classifier(path, catalog_sha256=CATALOG_DIGEST, document_count=3)
    with pytest.raises(ArtifactError):
        load_lexical_index(path)
    with pytest.raises(ArtifactError):
        load_embedding_index(path)


def test_a_classifier_with_an_extra_section_is_refused(tmp_path: Path) -> None:
    """Its section set is fixed; an unexpected one is not ignored."""

    path = tmp_path / "classifier.mgdx"
    _valid_classifier(path, catalog_sha256=CATALOG_DIGEST, document_count=3)
    artifact = read_artifact(path)
    sections = dict(artifact.sections)
    sections["surprise"] = b"payload"
    header = {
        key: value
        for key, value in artifact.header.items()
        if key != "artifact_schema_version"
    }
    write_artifact(path, header, sections)
    with pytest.raises(ArtifactError, match="sections do not match its schema"):
        load_classifier(path)


def test_a_classifier_with_a_truncated_float_table_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "classifier.mgdx"
    _valid_classifier(path, catalog_sha256=CATALOG_DIGEST, document_count=3)
    artifact = read_artifact(path)
    sections = dict(artifact.sections)
    sections["idf"] = sections["idf"][:-1]
    header = {
        key: value
        for key, value in artifact.header.items()
        if key != "artifact_schema_version"
    }
    write_artifact(path, header, sections)
    with pytest.raises(ArtifactError, match="truncated"):
        load_classifier(path)


def test_a_classifier_whose_declared_class_count_lies_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "classifier.mgdx"
    _valid_classifier(path, catalog_sha256=CATALOG_DIGEST, document_count=3)
    artifact = read_artifact(path)
    header = {
        key: value
        for key, value in artifact.header.items()
        if key != "artifact_schema_version"
    }
    header["class_count"] = 7
    write_artifact(path, header, dict(artifact.sections))
    with pytest.raises(ArtifactError, match="class count does not match"):
        load_classifier(path)


# --------------------------------------------------------------------------
# The runtime keeps no arbitrary-code deserializer
# --------------------------------------------------------------------------


def test_the_public_runtime_never_imports_pickle_or_joblib() -> None:
    """A frozen artifact is parsed, never executed.

    `data/models/` ships inside a public image. If any runtime module could be
    talked into unpickling one of those files, a corrupted artifact would be
    remote code execution rather than a load error.
    """

    root = Path(__file__).resolve().parents[1] / "src" / "mandateguard"
    runtime = [
        path
        for path in root.rglob("*.py")
        # `ml/` is the offline trainer; it is not in the serving image.
        if "\\ml\\" not in str(path) and "/ml/" not in str(path)
    ]
    offenders: list[str] = []
    for path in runtime:
        text = path.read_text(encoding="utf-8")
        for banned in ("import pickle", "import joblib", "import dill", "import marshal"):
            if banned in text:
                offenders.append(f"{path.name}: {banned}")
    assert not offenders, offenders
