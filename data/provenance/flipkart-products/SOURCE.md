# Source: Flipkart Products (PromptCloud)

The discovery catalog in `data/processed/` is a normalized derivative of one
public Kaggle dataset. This file records what was retrieved, from where, and
with which digests, so the claim can be checked rather than believed.

## Dataset identity

| Field | Value |
| --- | --- |
| Dataset title | **Flipkart Products** |
| Kaggle ref | `PromptCloudHQ/flipkart-products` |
| Publisher / owner | PromptCloud (`organizations/PromptCloudHQ`) |
| Creator | PromptCloud (`promptcloud`) |
| Kaggle URL | <https://www.kaggle.com/datasets/PromptCloudHQ/flipkart-products> |
| Subtitle | 20,000 products on Flipkart |
| Upstream version | 1 |
| Upstream last updated | 2017-09-15T09:35:02.313Z |
| **Licence** | **CC BY-SA 4.0** (`licenseName` as reported upstream) |
| Licence deed | <https://creativecommons.org/licenses/by-sa/4.0/> |
| Licence legal code | <https://creativecommons.org/licenses/by-sa/4.0/legalcode> |

The licence identifier is **not asserted by this repository**. It is taken from
the upstream response preserved verbatim in [`kaggle-metadata.json`](kaggle-metadata.json),
retrieved from Kaggle's public, unauthenticated dataset listing API:

```bash
curl 'https://www.kaggle.com/api/v1/datasets/list?search=flipkart-products'
```

The dataset landing page is client-rendered and returns no licence text to a
plain fetch, so the listing API is the reproducible machine-readable source.
Re-running the command above and selecting the entry whose `ref` is
`PromptCloudHQ/flipkart-products` reproduces the snapshot.

## Retrieval

| Field | Value |
| --- | --- |
| Retrieval date (archive) | 2026-09-03 |
| Retrieval date (metadata snapshot) | see `retrieval.retrieved_at` in `kaggle-metadata.json` |
| Download URL | `https://www.kaggle.com/api/v1/datasets/download/PromptCloudHQ/flipkart-products` |
| Archive | `data/import/flipkart-raw.zip` (not committed; see below) |
| Archive size | 5,765,116 bytes |
| **Archive SHA-256** | `54a91fcd0b3d1923e3adb52c27e4dde557a7cd948dba066e3cb5bca542da1b9f` |
| Member file | `flipkart_com-ecommerce_sample.csv` |
| Member size | 38,114,963 bytes |
| **Member SHA-256** | `56f8f699c9e847356666c2eab3c3ab1244340f6a98ad08e39ea2199ebe993ad1` |
| **Upstream row count** | 20,000 data rows (header excluded) |
| Crawl timestamp range | 2015-12-01 to 2016-06-28 (UTC), from the `crawl_timestamp` column |

Upstream reports `totalBytes` 5,765,116 for this dataset. The archive retrieved
here is byte-for-byte that size, which is recorded as
`byte_size_matches_upstream` in the metadata snapshot.

The raw archive is **not committed**. It is 5.7 MB of upstream bytes that
`scripts/import_discovery_catalog.py` can fetch again, and the digests above are
what make the un-committed file verifiable. What the repository carries is the
normalized derivative plus this provenance record.

## What the data is, and is not

* These are **crawled marketplace listings**, captured between December 2015 and
  June 2016. Every price in the catalog is a **historical listing price from that
  window**. It is not a current offer, and the product surface labels it as
  historical wherever it is displayed.
* A listing is a claim made by a crawl. It is **not** merchant evidence, and it
  can never become merchant evidence by resembling one. Nothing sourced from
  this dataset is transactable in MandateGuard: crawled listings terminate at
  `REVIEW REQUIRED`.
* Of the 17,702 rows in the built catalog, 17,694 come from this dataset and 8
  are separately registered MandateGuard merchant products. Only those 8 carry
  authoritative merchant evidence.

## Files in this directory

| File | What it is |
| --- | --- |
| `SOURCE.md` | This record. |
| `kaggle-metadata.json` | The upstream Kaggle API response, verbatim, plus local digest verification. |
| `LICENSE_NOTICE.md` | The CC BY-SA 4.0 attribution and share-alike notice for the derivative. |
| `TRANSFORMATIONS.md` | Exactly how the normalized catalog was produced from the archive. |
