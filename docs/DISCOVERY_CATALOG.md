# Discovery catalog

The commerce universe MandateGuard reasons about, and where it came from.

---

## What this is, and what it is not

The discovery catalog is a **listing** corpus: things an agent can find and
reason about. It is not merchant authorization evidence, and nothing in it can
satisfy a Tier A/B check or a semantic constraint.

That boundary is not a convention. It is asserted in code
([`src/mandateguard/discovery/trust.py`](../src/mandateguard/discovery/trust.py))
and tested
([`tests/test_discovery_trust_boundary.py`](../tests/test_discovery_trust_boundary.py)),
including a test that fails if any module under `mandateguard.discovery` ever
imports the authorization or execution path.

> ML understands the commerce universe.
> MandateGuard's deterministic gate controls money.

---

## Source and licensing

| Item | Value |
| --- | --- |
| Dataset | Flipkart Products (pre-crawled sample) |
| Publisher | PromptCloud / DataStock |
| Landing page | <https://www.kaggle.com/datasets/PromptCloudHQ/flipkart-products> |
| Licence | **CC BY-SA 4.0** (<https://creativecommons.org/licenses/by-sa/4.0/>) |
| Upstream rows | 20,000 |
| Crawled | 2015–2016 |
| Archive SHA-256 | `54a91fcd0b3d1923e3adb52c27e4dde557a7cd948dba066e3cb5bca542da1b9f` |
| Member CSV SHA-256 | `56f8f699c9e847356666c2eab3c3ab1244340f6a98ad08e39ea2199ebe993ad1` |

**The licence is not asserted here.** It is taken from Kaggle's public,
unauthenticated dataset listing API, whose response is preserved verbatim in
[`data/provenance/flipkart-products/kaggle-metadata.json`](../data/provenance/flipkart-products/kaggle-metadata.json)
together with the digests of the archive actually imported. Upstream reports
`totalBytes` 5,765,116 for this dataset; the archive retrieved here is exactly
that size, and the snapshot records the match. Reproduce with:

```bash
curl 'https://www.kaggle.com/api/v1/datasets/list?search=flipkart-products'
```

**Attribution.** Flipkart Products dataset by PromptCloud, published on Kaggle
under CC BY-SA 4.0. Redistributed here in normalized form under the same
licence. The normalized catalog in `data/processed/` is a derivative work and
carries the same terms.

Committed provenance evidence lives in
[`data/provenance/flipkart-products/`](../data/provenance/flipkart-products/):

| File | What it is |
| --- | --- |
| `SOURCE.md` | Dataset identity, retrieval date, digests, upstream row count, exact licence identifier |
| `kaggle-metadata.json` | The upstream API response, verbatim, plus local digest verification |
| `LICENSE_NOTICE.md` | The CC BY-SA 4.0 attribution and share-alike notice for the derivative |
| `TRANSFORMATIONS.md` | Every change made to the upstream work, rule by rule |

That directory ships inside the public container image alongside
`data/processed/`, so the attribution travels with the derived bytes rather than
living only in this repository. This project claims **no rights in the upstream
data** beyond those CC BY-SA 4.0 grants.

The raw archive is **not committed**. It lands in `data/import/`, which is
git-ignored, and is reproducible with one command. What the repository carries
is the normalized catalog plus a manifest recording exactly which upstream bytes
produced it.

A second source is merged into the same catalog: the eight application-registered
products in `fixtures/agentic_commerce/merchant_catalog.json`. They are indexed
alongside the crawled listings, in the same index, ranked by the same scorer.
That is deliberate — see [Why both sources share one index](#why-both-sources-share-one-index).

### Prices are historical

The prices are 2016 INR listing prices. They are treated as **listing claims**
for discovery and budget filtering only, and never as authoritative merchant
price evidence. Tier A's authoritative-price check does not read this catalog.

---

## Normalized schema

Every row is validated against
[`DiscoveryProduct`](../src/mandateguard/discovery/schema.py) before it is
committed:

| Field | Notes |
| --- | --- |
| `catalog_product_id` | `sha256(source \| source_product_id)`, so a re-import reproduces the same ids |
| `source` | `flipkart` or `mandateguard` |
| `source_product_id` | upstream `uniq_id`, or `merchant_id/sku` for registered products |
| `title` | whitespace-collapsed, ≤ 400 chars |
| `description` | normalized, ≤ 2,000 chars |
| `brand` | `null` when the source omits it |
| `category_path` | parsed from the source's `>>`-delimited tree, ≤ 6 segments |
| `price_minor` | integer paise, `null` when the source publishes none |
| `currency` | ISO-4217 |
| `merchant_or_seller` | the *listing platform*, not a seller of record |
| `rating` | `null` unless numeric and within `[0, 5]` |
| `product_url` | `null` unless absolute |
| `raw_source_sha256` | commits to the exact upstream row |

A field the source omits stays `null`. Nothing is inferred to fill a gap: an
unknown value is reported as unresolved by the transactability diagnostic, which
is the honest answer and also the more useful one.

### Normalizations applied, and why

| Rule | Rows affected | Reason |
| --- | ---: | --- |
| Strip a leading verbatim repetition of the title from the description | 11,052 | Otherwise every listing looks self-consistent to the title/description agreement feature |
| Strip the inlined `Price: Rs. N` fragment | 6,580 | A stale 2016 figure restated in prose; it pollutes both the index and the price features |
| Demote a top-level segment used fewer than 25 times to `Uncategorized` | 400 | In the source, a malformed row repeats the product name in the category column. Those are not taxonomy nodes |
| Drop rows identical in title, description, brand, **and** price | 2,306 | The same listing published twice. Sibling SKUs that differ in any of those fields are kept: a real catalogue contains variants, and collapsing them would understate the retrieval problem |

---

## What the import produces

```
17,702 listings
    17,694 from the Flipkart crawl
         8 registered merchant products

    26 top-level categories
 6,338 distinct category paths
 3,370 distinct brands
17,626 listings with a published price (76 without)

 4.71 MB  data/processed/discovery_catalog.jsonl.gz
```

The gzip archive is written with `mtime=0`, so re-importing the same rows
produces byte-identical output and therefore byte-identical indexes.

---

## Reproducing it

```bash
python scripts/import_discovery_catalog.py
```

Downloads the registered archive to `data/import/`, normalizes, deduplicates,
merges both sources, and writes `data/processed/` with its manifest. Pass
`--no-download` to use an archive you placed there yourself.

Then build the frozen artifacts (needs `requirements-train.txt`):

```bash
python scripts/train_discovery_models.py
```

---

## Why both sources share one index

The whole point of the transactability diagnostic is only visible when both
kinds of listing appear in one result list and the difference between them is a
property of the *listing*, not of the page it is on. Search "a study lamp under
₹2,000" and you get a registered product that reads `EVIDENCE READY` next to a
crawled one that reads `REVIEW REQUIRED`, ranked by the same scorer, with the
reason for the difference spelled out on each card.

Indexing a registered product for discovery does **not** expose its trusted
evidence. Evidence resolves only through `TrustedCommerceStore`, keyed by
merchant and SKU. The one channel between the two worlds is
`TrustedListingFacts`, which carries counts and identities and never evidence
text — there is a test asserting exactly which fields it may contain.

---

## The importer refuses unregistered data

A dataset is only importable if its licence, publisher, and retrieval URL are
declared in
[`ingest/sources.py`](../src/mandateguard/discovery/ingest/sources.py). There is
no path that ingests an arbitrary file, so a dataset cannot enter the repository
without its provenance entering with it.
