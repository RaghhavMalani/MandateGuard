"""Registered public dataset sources, with provenance recorded up front.

A source is only importable if its licence, publisher, and retrieval URL are
declared here. The importer refuses an unregistered file, so a dataset cannot
enter the repository without its provenance entering with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class DatasetSource:
    """Provenance and licensing for one importable public dataset."""

    source_id: str
    display_name: str
    publisher: str
    landing_page: str
    download_url: str
    licence: str
    licence_url: str
    attribution: str
    member_filename: str
    archive: bool
    adapter: str
    expected_rows: int
    notes: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "publisher": self.publisher,
            "landing_page": self.landing_page,
            "download_url": self.download_url,
            "licence": self.licence,
            "licence_url": self.licence_url,
            "attribution": self.attribution,
            "member_filename": self.member_filename,
            "archive": self.archive,
            "adapter": self.adapter,
            "expected_rows": self.expected_rows,
            "notes": self.notes,
        }


FLIPKART_PROMPTCLOUD_2016: Final[DatasetSource] = DatasetSource(
    source_id="flipkart",
    display_name="Flipkart Products (pre-crawled sample)",
    publisher="PromptCloud / DataStock",
    landing_page="https://www.kaggle.com/datasets/PromptCloudHQ/flipkart-products",
    download_url=(
        "https://www.kaggle.com/api/v1/datasets/download/PromptCloudHQ/flipkart-products"
    ),
    licence="CC BY-SA 4.0",
    licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
    attribution=(
        "Flipkart Products dataset by PromptCloud, published on Kaggle under "
        "CC BY-SA 4.0. Crawled from flipkart.com in 2015-2016. Redistributed "
        "here in normalized form under the same licence."
    ),
    member_filename="flipkart_com-ecommerce_sample.csv",
    archive=True,
    adapter="flipkart_promptcloud_csv",
    expected_rows=20_000,
    notes=(
        "20,000-row public subset of a larger PromptCloud crawl. Prices are 2016 "
        "INR listing prices and are historical: they are treated as listing "
        "claims for discovery only, never as authoritative merchant price "
        "evidence."
    ),
)


MANDATEGUARD_REGISTRY: Final[DatasetSource] = DatasetSource(
    source_id="mandateguard",
    display_name="Registered MandateGuard merchant catalog",
    publisher="This repository",
    landing_page="fixtures/agentic_commerce/merchant_catalog.json",
    download_url="",
    licence="Repository fixture",
    licence_url="",
    attribution=(
        "The application-registered products the authorization controller "
        "already knows about, indexed alongside the crawled catalog so the two "
        "kinds of listing appear in one result list."
    ),
    member_filename="merchant_catalog.json",
    archive=False,
    adapter="mandateguard_registry_json",
    expected_rows=8,
    notes=(
        "Indexing a registered product for discovery does not expose its trusted "
        "evidence. Evidence resolves only through TrustedCommerceStore, by "
        "merchant and SKU."
    ),
)


REGISTERED_SOURCES: Final[dict[str, DatasetSource]] = {
    FLIPKART_PROMPTCLOUD_2016.source_id: FLIPKART_PROMPTCLOUD_2016,
    MANDATEGUARD_REGISTRY.source_id: MANDATEGUARD_REGISTRY,
}

#: The sources a default import merges into one catalog, in index order.
DEFAULT_SOURCE_IDS: Final[tuple[str, ...]] = ("flipkart", "mandateguard")


def get_source(source_id: str) -> DatasetSource:
    try:
        return REGISTERED_SOURCES[source_id]
    except KeyError as error:
        raise ValueError(
            f"unregistered dataset source: {source_id!r}; "
            f"registered: {sorted(REGISTERED_SOURCES)}"
        ) from error
