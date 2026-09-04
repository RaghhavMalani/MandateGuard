"""Download pinned candidate artifacts and write provenance before evaluation.

No inference or evaluation is performed by this script.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys

from huggingface_hub import hf_hub_download


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "semantic-v2"
PROVENANCE = ROOT / "data" / "provenance" / "semantic-models"


CANDIDATES = (
    {
        "model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "license": "Apache-2.0",
        "dimension": 384,
        "maximum_sequence_length": 256,
        "tokenizer": "BertTokenizerFast / WordPiece, uncased, vocab size 30,522",
        "pooling": "attention-mask mean pooling",
        "normalization": "L2",
        "query_prefix": "",
        "document_prefix": "",
        "intended_usage": "sentence and paragraph embeddings for semantic search, clustering, and similarity",
        "onnx_file": "onnx/model.onnx",
        "model_card_url": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2",
    },
    {
        "model_id": "BAAI/bge-small-en-v1.5",
        "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "license": "MIT",
        "dimension": 384,
        "maximum_sequence_length": 512,
        "tokenizer": "BertTokenizer / WordPiece, uncased, vocab size 30,522",
        "pooling": "CLS token",
        "normalization": "L2",
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "document_prefix": "",
        "intended_usage": "English retrieval embeddings; query instruction is used for short-query to passage retrieval",
        "onnx_file": "onnx/model.onnx",
        "model_card_url": "https://huggingface.co/BAAI/bge-small-en-v1.5",
    },
    {
        "model_id": "intfloat/e5-small-v2",
        "revision": "ffb93f3bd4047442299a41ebb6fa998a38507c52",
        "license": "MIT",
        "dimension": 384,
        "maximum_sequence_length": 512,
        "tokenizer": "BertTokenizerFast / WordPiece, uncased, vocab size 30,522",
        "pooling": "attention-mask mean pooling",
        "normalization": "L2",
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
        "intended_usage": "English text embeddings for retrieval with mandatory query:/passage: prefixes",
        "onnx_file": "onnx/model_O4.onnx",
        "model_card_url": "https://huggingface.co/intfloat/e5-small-v2",
    },
)


METADATA_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
)


def file_record(path: Path, relative: str) -> dict[str, object]:
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def main() -> int:
    PROVENANCE.mkdir(parents=True, exist_ok=True)
    for candidate in CANDIDATES:
        model_id = str(candidate["model_id"])
        revision = str(candidate["revision"])
        slug = model_id.replace("/", "--")
        local_dir = CACHE / slug
        records = []
        for filename in (*METADATA_FILES, str(candidate["onnx_file"])):
            downloaded = Path(
                hf_hub_download(
                    repo_id=model_id,
                    filename=filename,
                    revision=revision,
                    local_dir=local_dir,
                )
            )
            records.append(file_record(downloaded, filename))
        tokenizer = next(item for item in records if item["path"] == "tokenizer.json")
        onnx = next(item for item in records if item["path"] == candidate["onnx_file"])
        provenance = {
            "schema_version": "semantic-model-provenance-v1",
            "verified_before_evaluation": True,
            "authoritative_source": "Hugging Face repository owned by the model publisher",
            **candidate,
            "pinned_model_card_url": f"{candidate['model_card_url']}/tree/{revision}",
            "license_source": f"{candidate['model_card_url']}/tree/{revision}",
            "tokenizer_identity": {
                "description": candidate["tokenizer"],
                "tokenizer_json_sha256": tokenizer["sha256"],
            },
            "model_sha256": onnx["sha256"],
            "files": records,
            "unsafe_serialization": False,
            "runtime_external_calls": 0,
        }
        output = PROVENANCE / f"{slug}.json"
        output.write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"prepared {model_id}@{revision} -> {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
