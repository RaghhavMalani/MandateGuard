# Transformations — upstream CSV to normalized catalog

This is the complete list of changes made to the upstream work, as CC BY-SA 4.0
requires a derivative to state. Every rule is mechanical and deterministic: none
of them invents a value the source did not contain. A field the source omits
stays `null` and is reported as unresolved by the transactability diagnostic
rather than filled in.

Reproduce with:

```bash
python scripts/import_discovery_catalog.py
```

Implementation: `src/mandateguard/discovery/ingest/` — `flipkart.py` (adapter),
`normalize.py` (field rules), `pipeline.py` (dedup and manifest).

## Row accounting

| Stage | Rows |
| --- | --- |
| Upstream CSV data rows | 20,000 |
| Normalized (title and `uniq_id` both present) | 20,000 |
| Dropped as exact duplicates | 2,306 |
| Committed from this dataset | **17,694** |
| Registered MandateGuard merchant products merged in | 8 |
| **Total catalog listings** | **17,702** |

The 8 registered products do **not** come from this dataset and are not covered
by its licence; they are MandateGuard's own fixtures and are the only listings in
the catalog that carry merchant evidence.

## Field-level rules

| Output field | Source column | Rule |
| --- | --- | --- |
| `catalog_product_id` | `uniq_id` | `sha256("flipkart" \| uniq_id)`, truncated. A pure function of source identity, so re-import reproduces the same ids and the same frozen index offsets. |
| `source` | — | Constant `"flipkart"`. |
| `source_product_id` | `uniq_id` | Whitespace-collapsed, control characters removed. |
| `title` | `product_name` | Whitespace-collapsed, control characters removed, truncated to 400 characters. |
| `description` | `description` | See *Description rewriting* below. |
| `brand` | `brand` | Whitespace-collapsed; empty becomes `null`. |
| `category_path` | `product_category_tree` | Python-literal `["A >> B >> C"]` parsed, split on `>>`, whitespace-collapsed, capped at 6 segments. See *Taxonomy floor*. |
| `price_minor` | `discounted_price`, then `retail_price` | First parsable value, `×100` to integer minor units (INR paise). Negative, NaN, and infinite values are rejected, leaving `null`. |
| `currency` | — | Constant `"INR"`. The source is a single-currency Indian marketplace export. |
| `merchant_or_seller` | — | Constant `"flipkart.com"`. This is the **listing platform, not a seller of record**: the export carries no seller identity, and that absence is what the transactability diagnostic reports. |
| `rating` | `product_rating` | Parsed as a float, kept only within `[0, 5]`, rounded to 2 decimals; otherwise `null`. |
| `product_url` | `product_url` | Kept only if it begins with `http://` or `https://`; otherwise `null`. |
| `raw_source_sha256` | whole row | SHA-256 of the canonical JSON of the entire upstream row, so each listing commits to the exact row it came from. |

Columns present upstream and **not** carried into the catalog: `crawl_timestamp`,
`pid`, `image`, `is_FK_Advantage_product`, `overall_rating`,
`product_specifications`.

## Description rewriting

Two source artefacts are removed from `description`, and both removals are
counted in the manifest:

1. **Verbatim title prefix** (11,052 rows). Many descriptions open by repeating
   the product title. Left in place, every listing would look self-consistent to
   the title/description agreement feature, which would make that feature
   measure the crawler rather than the product.
2. **Inlined `Price: Rs. N` fragment** (6,580 rows). The crawler inlined a price
   into the prose. It is a 2015–2016 figure and is never the authoritative price,
   so it is stripped rather than left where a reader might take it as current.

The result is whitespace-collapsed and truncated to 2,000 characters on a word
boundary.

## Taxonomy floor

A malformed upstream row repeats the product name in the category column,
producing hundreds of one-off "top-level categories". A top segment used fewer
than 25 times in the source is not treated as a taxonomy node: it is demoted
under `Uncategorized` and kept as a lower segment. This affected **400 rows**.

## Deduplication

Two listings are dropped as duplicates only when **all four** of title,
description, brand, and price match exactly after normalization. 2,306 rows were
removed this way. Listings that differ in price — the same product at two
prices — are two offers and both are kept.

(This is the *import-time* rule. Search-time near-duplicate suppression is a
separate mechanism in `discovery/index/hybrid.py` and is also conservative:
it never collapses two listings on title text alone.)

## Prices are historical

Every price in this catalog is a listing price captured between **2015-12-01 and
2016-06-28 UTC**. It is not a current offer and is not merchant price evidence.
The product surface labels these values as historical wherever it renders them,
and the authorization controller never accepts one as a price claim.

## What was not done

* No rows were edited to change meaning, no values were imputed, no prices were
  adjusted, converted, or inflation-corrected.
* No listing was promoted to trusted or transactable status. Crawled listings
  terminate at `REVIEW REQUIRED` by construction.
* No upstream row was excluded for its content; the only removals are the
  duplicate rule above and rows lacking a `uniq_id` or a title (of which there
  were none).
