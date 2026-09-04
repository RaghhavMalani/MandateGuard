# Licence notice — derived data

## Upstream work

**Flipkart Products**, by **PromptCloud**, published on Kaggle at
<https://www.kaggle.com/datasets/PromptCloudHQ/flipkart-products>, licensed under
**Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)**.

* Deed: <https://creativecommons.org/licenses/by-sa/4.0/>
* Legal code: <https://creativecommons.org/licenses/by-sa/4.0/legalcode>

The licence identifier is recorded upstream and preserved verbatim in
[`kaggle-metadata.json`](kaggle-metadata.json). See [`SOURCE.md`](SOURCE.md).

## This derivative

`data/processed/discovery_catalog.jsonl.gz` and its manifest are an **adapted
version** of that work: rows were parsed, normalized, deduplicated, and re-encoded
as described in [`TRANSFORMATIONS.md`](TRANSFORMATIONS.md). The frozen indexes in
`data/models/` are built from that derivative and encode its text.

Because the upstream licence is CC BY-SA 4.0, this derivative is distributed
**under the same licence**: CC BY-SA 4.0.

### Required attribution

> Contains information from the **Flipkart Products** dataset by **PromptCloud**
> (<https://www.kaggle.com/datasets/PromptCloudHQ/flipkart-products>), used under
> [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The data has
> been normalized and deduplicated by the MandateGuard project and is
> redistributed under CC BY-SA 4.0.

### What this repository does and does not claim

* This project claims **no rights in the upstream data** beyond those CC BY-SA 4.0
  grants, and asserts no additional restriction on the derivative.
* Changes were made. They are enumerated in `TRANSFORMATIONS.md` rather than
  summarized, so a downstream user can tell exactly what is upstream and what is
  ours.
* This notice covers the **dataset only**. MandateGuard's own source code is
  separate from it and is not placed under CC BY-SA 4.0 by this notice.
* The trademarks and product names appearing in listing text belong to their
  respective owners. Their presence in a crawled catalog is not endorsement,
  affiliation, or a merchant relationship, and MandateGuard treats none of them
  as merchant evidence.

### Where this notice travels

The public container image copies `data/provenance/` alongside
`data/processed/`, so the attribution ships wherever the derived bytes ship
rather than living only in the repository.
