"""Create the outcome-free semantic discovery v2 evaluation freeze.

This authoring script deliberately has no imports from retrieval or model code.
It may inspect no ranked lists and writes no measured outcomes.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "semantic-v2"
CATALOG_SHA256 = "7ad5d4f579eca4838eabca2de755003e0b513ff4213af683b4574559421ae09f"
BASE_COMMIT = "a72ec972714ef4af8319ad015916641e13cd36c9"


def target(
    key: str,
    literal: str,
    paraphrase: str,
    note: str,
    *,
    title_any: tuple[str, ...],
    categories: tuple[str, ...] = (),
    any_terms: tuple[str, ...] = (),
    all_terms: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    brands: tuple[str, ...] = (),
    max_price_minor: int | None = None,
) -> dict[str, object]:
    relevance: dict[str, object] = {"require_title_any": list(title_any)}
    if categories:
        relevance["categories"] = list(categories)
    if any_terms:
        relevance["require_any_terms"] = list(any_terms)
    if all_terms:
        relevance["require_all_terms"] = list(all_terms)
    if exclude:
        relevance["exclude_terms"] = list(exclude)
    if brands:
        relevance["brands"] = list(brands)
    if max_price_minor is not None:
        relevance["max_price_minor"] = max_price_minor
    return {
        "key": key,
        "literal": literal,
        "paraphrase": paraphrase,
        "note": note,
        "relevance": relevance,
    }


TARGETS = (
    target("HEADPHONES", "wired headphones under Rs 5000", "I want to listen privately on the train without spending more than five thousand", "Headphones or earphones within budget.", title_any=("headphone", "earphone", "headset"), any_terms=("headphone", "earphone", "headset"), max_price_minor=500000),
    target("BRACELET", "silver bracelet for women under Rs 2000", "a silver-coloured gift she can wear around her wrist for less than two thousand", "Silver bracelet listings.", title_any=("bracelet", "bangle"), categories=("Jewellery",), all_terms=("silver",), max_price_minor=200000),
    target("CAR_PHONE_HOLDER", "car mobile phone holder under Rs 500", "something that keeps my phone steady while I am driving and costs at most five hundred", "Car phone mounts and holders.", title_any=("mount", "holder", "cradle"), categories=("Automotive",), any_terms=("phone", "mobile"), max_price_minor=50000),
    target("KURTA", "women's printed kurta under Rs 800", "an inexpensive traditional tunic for her to wear to a family lunch", "Kurta and kurti listings.", title_any=("kurta", "kurti"), categories=("Clothing", "Uncategorized"), max_price_minor=80000),
    target("MENS_WATCH", "analog wrist watch for men under Rs 3000", "a present that lets him check the time without taking out his phone, below three thousand", "Men's watches.", title_any=("watch",), categories=("Watches",), any_terms=("men", "boys"), max_price_minor=300000),
    target("RUNNING_SHOES", "men's running shoes under Rs 3000", "footwear for morning jogs for him without going above three thousand", "Men's running or sports shoes.", title_any=("running", "sports"), categories=("Footwear",), any_terms=("shoe",), max_price_minor=300000),
    target("SCHOOL_BACKPACK", "school backpack under Rs 1500", "my child starts school next month and needs to carry books for under fifteen hundred", "School bags and backpacks.", title_any=("backpack", "school bag"), categories=("Bags, Wallets & Belts", "Toys & School Supplies", "Uncategorized"), max_price_minor=150000),
    target("WALL_CLOCK", "decorative wall clock under Rs 1500", "I keep losing track of time in the living room and have fifteen hundred to spend", "Wall clocks.", title_any=("wall clock",), categories=("Home Decor & Festive Needs",), max_price_minor=150000),
    target("BEDSHEET", "cotton double bedsheet under Rs 2000", "fresh fabric to cover a double bed, capped at two thousand", "Double bed sheets.", title_any=("bedsheet", "bed sheet"), categories=("Home Furnishing",), max_price_minor=200000),
    target("PHONE_CASE", "mobile phone case under Rs 500", "protect my handset from scratches and drops for no more than five hundred", "Phone cases and covers.", title_any=("cover", "case"), categories=("Mobiles & Accessories",), max_price_minor=50000),
    target("EARRINGS", "gold plated earrings under Rs 1000", "a small gold-coloured jewellery gift worn on the ears below one thousand", "Earrings, studs, and jhumki.", title_any=("earring", "jhumki", "stud"), categories=("Jewellery",), max_price_minor=100000),
    target("MENS_TSHIRT", "men's round neck t-shirt under Rs 700", "a casual top for him to wear on weekends costing at most seven hundred", "Men's T-shirts.", title_any=("t-shirt", "tshirt"), categories=("Clothing", "Uncategorized"), any_terms=("men", "boys"), max_price_minor=70000),
    target("CUSHION_COVER", "printed cushion cover under Rs 900", "our sofa looks tired and I want to freshen its pillows cheaply", "Cushion covers.", title_any=("cushion",), categories=("Home Furnishing", "Home Decor & Festive Needs"), max_price_minor=90000),
    target("SUNGLASSES", "aviator sunglasses under Rs 2000", "something for my eyes because afternoon sunlight is too bright, below two thousand", "Sunglasses.", title_any=("sunglass",), categories=("Sunglasses", "Uncategorized"), max_price_minor=200000),
    target("LAPTOP_BAG", "laptop backpack under Rs 2000", "something to carry my computer safely to the office for less than two thousand", "Laptop bags, backpacks, and sleeves.", title_any=("bag", "backpack", "sleeve"), all_terms=("laptop",), max_price_minor=200000),
    target("KITCHEN_KNIFE", "stainless steel kitchen knife under Rs 1000", "a sharp kitchen tool for chopping vegetables under one thousand", "Kitchen knives.", title_any=("knife", "knives"), categories=("Kitchen & Dining",), max_price_minor=100000),
    target("CRICKET_BAT", "cricket bat under Rs 3000", "the main piece of equipment needed to score runs, within three thousand", "Cricket bats.", title_any=("bat",), categories=("Sports & Fitness", "Toys & School Supplies"), all_terms=("cricket",), max_price_minor=300000),
    target("YOGA_MAT", "yoga mat under Rs 2000", "I need padding to stretch and exercise on the floor at home", "Yoga mats.", title_any=("mat",), categories=("Sports & Fitness",), all_terms=("yoga",), max_price_minor=200000),
    target("POWER_BANK", "mobile power bank under Rs 2000", "my phone dies halfway through the day while travelling and I have two thousand", "Portable battery packs.", title_any=("power bank", "powerbank", "mah"), categories=("Mobiles & Accessories", "Computers", "Uncategorized"), max_price_minor=200000),
    target("SCREWDRIVER", "screwdriver bit set under Rs 1000", "a compact hand tool set for tightening loose screws below one thousand", "Screwdrivers and driver bit sets.", title_any=("screwdriver",), categories=("Tools & Hardware",), max_price_minor=100000),
    target("SAREE", "printed silk saree under Rs 2000", "traditional draped clothing for a family wedding within two thousand", "Saree and sari listings.", title_any=("saree", "sari"), categories=("Clothing", "Uncategorized"), max_price_minor=200000),
    target("PET_SHAMPOO", "dog shampoo under Rs 500", "my puppy needs a bath and I can spend five hundred", "Dog and pet shampoo.", title_any=("shampoo",), categories=("Pet Supplies",), any_terms=("dog", "pet"), max_price_minor=50000),
    target("PEN_SET", "ball pen set under Rs 200", "inexpensive writing tools for taking notes, no pencils", "Pens excluding pencil boxes.", title_any=("pen",), categories=("Pens & Stationery",), exclude=("pencil box",), max_price_minor=20000),
    target("DIAPER", "baby cloth diaper under Rs 1000", "reusable absorbent clothing for a baby for less than one thousand", "Baby diapers and nappies.", title_any=("diaper", "nappy"), categories=("Baby Care",), max_price_minor=100000),
    target("READING_LAMP", "study table lamp under Rs 2000", "I need something so I can keep reading after my roommate sleeps", "Desk lamps and reading lights, excluding decorative rice lights.", title_any=("lamp", "reading light"), categories=("Home Decor & Festive Needs", "Home Improvement", "Computers", "Uncategorized"), exclude=("rice light",), max_price_minor=200000),
    target("KEYBOARD", "USB wired keyboard under Rs 2000", "keys I can plug into my computer for typing, below two thousand", "Computer keyboards.", title_any=("keyboard",), categories=("Computers",), max_price_minor=200000),
    target("WATER_BOTTLE", "stainless steel water bottle under Rs 500", "a reusable container to carry drinking water for five hundred or less", "Water bottles.", title_any=("bottle",), categories=("Kitchen & Dining", "Sports & Fitness", "Home Furnishing"), max_price_minor=50000),
    target("BLUETOOTH_SPEAKER", "Bluetooth wireless speaker under Rs 3000", "portable audio I can play aloud from my phone without a cable", "Bluetooth speakers and audio receivers.", title_any=("speaker", "receiver"), all_terms=("bluetooth",), max_price_minor=300000),
    target("UMBRELLA", "rain umbrella under Rs 1000", "something handheld to keep me dry walking home in the monsoon", "Umbrellas.", title_any=("umbrella",), max_price_minor=100000),
    target("CAR_FLOOR_MAT", "car floor mat set under Rs 2000", "protect the carpet under everyone's feet in my vehicle", "Automotive floor mats.", title_any=("mat",), categories=("Automotive",), any_terms=("car", "auto", "vehicle"), max_price_minor=200000),
    target("LAPTOP_COOLER", "laptop cooling fan under Rs 2000", "something to keep my laptop cool while gaming", "USB fans explicitly associated with laptops or cooling.", title_any=("fan", "cooler"), categories=("Computers", "Home Improvement", "Uncategorized"), any_terms=("laptop", "cooling"), max_price_minor=200000),
    target("WORKOUT_AUDIO", "wireless headphones for workouts under Rs 4000", "I want audio gear for workouts without spending more than four thousand", "Wireless personal audio within budget.", title_any=("headphone", "earphone", "headset"), any_terms=("wireless", "bluetooth"), max_price_minor=400000),
    target("PHOTO_GIFT", "camera accessory gift under Rs 3000", "gift for someone learning photography", "Camera accessories suitable for a beginner.", title_any=("lens", "camera", "tripod", "filter", "hood", "flash", "memory card"), categories=("Cameras & Accessories",), max_price_minor=300000),
    target("CAMERA_LENS", "camera lens under Rs 10000", "glass for changing what a camera can see without buying a new body", "Camera lenses and lens attachments.", title_any=("lens",), categories=("Cameras & Accessories",), max_price_minor=1000000),
    target("COMPUTER_MOUSE", "wireless computer mouse under Rs 1500", "a cordless pointing device for my laptop within fifteen hundred", "Computer mice.", title_any=("mouse",), categories=("Computers",), max_price_minor=150000),
    target("BIKE_HELMET", "motorcycle helmet under Rs 3000", "protective headgear for riding my bike below three thousand", "Riding helmets.", title_any=("helmet",), categories=("Automotive", "Sports & Fitness"), max_price_minor=300000),
    target("WALLET", "men's leather wallet under Rs 1500", "a pocket-sized place for his cash and cards costing under fifteen hundred", "Men's wallets.", title_any=("wallet",), categories=("Bags, Wallets & Belts",), max_price_minor=150000),
    target("PERFUME", "women's perfume under Rs 2000", "a fragrant gift she can wear for less than two thousand", "Perfume and eau de parfum.", title_any=("perfume", "parfum"), categories=("Beauty and Personal Care",), max_price_minor=200000),
    target("LIPSTICK", "matte lipstick under Rs 1000", "colour for her lips with a non-shiny finish below one thousand", "Matte lip colour.", title_any=("lipstick", "lip color", "lip colour"), categories=("Beauty and Personal Care",), max_price_minor=100000),
    target("HAIR_DRYER", "hair dryer under Rs 2000", "an electric appliance to dry wet hair quickly at home", "Hair dryers.", title_any=("hair dryer", "hair drier"), categories=("Health & Personal Care Appliances", "Beauty and Personal Care"), max_price_minor=200000),
    target("MIXER_GRINDER", "kitchen mixer grinder under Rs 5000", "an appliance to blend spices and make chutney within five thousand", "Mixer-grinders.", title_any=("mixer", "grinder"), categories=("Kitchen & Dining", "Home Improvement"), max_price_minor=500000),
    target("CURTAINS", "window curtains under Rs 2000", "fabric panels to block light coming through my windows", "Window curtains.", title_any=("curtain",), categories=("Home Furnishing",), max_price_minor=200000),
    target("OFFICE_CHAIR", "office chair under Rs 10000", "a seat with back support for working at a desk all day", "Office and desk chairs.", title_any=("chair",), categories=("Furniture",), max_price_minor=1000000),
    target("WIFI_ROUTER", "wireless Wi-Fi router under Rs 3000", "a box to share my internet connection around the house", "Network routers.", title_any=("router",), categories=("Computers",), max_price_minor=300000),
    target("PRINTER", "home inkjet printer under Rs 10000", "a machine for putting documents from my computer onto paper", "Computer printers.", title_any=("printer",), categories=("Computers",), max_price_minor=1000000),
    target("NOTEBOOK", "ruled notebook under Rs 500", "bound paper for handwritten class notes below five hundred", "Paper notebooks.", title_any=("notebook", "note book"), categories=("Pens & Stationery", "Toys & School Supplies"), max_price_minor=50000),
    target("FOOTBALL_SHOES", "football shoes under Rs 2000", "boots with grip for a weekend kickabout with friends", "Football footwear.", title_any=("football",), categories=("Footwear",), all_terms=("shoe",), max_price_minor=200000),
    target("GARDEN_PLANTER", "self-watering garden planter under Rs 1000", "a container that gives my plant water gradually while I am away", "Self-watering plant containers.", title_any=("planter", "plant container"), categories=("Tools & Hardware",), any_terms=("self watering", "self-watering"), max_price_minor=100000),
    target("PHONE_CHARGER", "mobile phone charger under Rs 1000", "a plug and cable to refill my handset battery", "Phone chargers and charging adapters.", title_any=("charger", "charging"), categories=("Mobiles & Accessories", "Computers"), max_price_minor=100000),
    target("RAINCOAT", "waterproof raincoat under Rs 1500", "clothing that keeps my body dry on a wet commute", "Raincoats.", title_any=("raincoat", "rain coat"), categories=("Clothing", "Automotive"), max_price_minor=150000),
    target("PILLOW", "sleeping pillow under Rs 1500", "soft support for my head in bed within fifteen hundred", "Bed pillows.", title_any=("pillow",), categories=("Home Furnishing",), max_price_minor=150000),
    target("COOKWARE", "non-stick cookware set under Rs 5000", "pots and pans that food will not cling to, below five thousand", "Non-stick cookware.", title_any=("cookware", "pan", "kadai"), categories=("Kitchen & Dining",), any_terms=("non-stick", "non stick"), max_price_minor=500000),
    target("VOYLLA_NECKLACE", "Voylla necklace", "neck jewellery made by Voylla", "Brand-constrained Voylla necklaces.", title_any=("necklace",), categories=("Jewellery",), brands=("Voylla",)),
    target("ALLURE_CAR_MAT", "Allure Auto car mat", "floor protection for a vehicle made by Allure Auto", "Brand-constrained Allure Auto car mats.", title_any=("mat",), categories=("Automotive",), brands=("Allure Auto",)),
    target("DAILYOBJECTS_COVER", "DailyObjects tablet cover", "protection for an iPad made by DailyObjects", "Brand-constrained DailyObjects covers.", title_any=("cover", "case"), categories=("Mobiles & Accessories",), brands=("DailyObjects",)),
    target("JJC_LENS_HOOD", "JJC lens hood", "a JJC attachment that shades camera glass", "Brand-constrained JJC lens hoods.", title_any=("lens hood", "hood"), categories=("Cameras & Accessories",), brands=("JJC",)),
    target("LAPGUARD_BATTERY", "Lapguard laptop battery", "a replacement notebook-computer power pack from Lapguard", "Brand-constrained Lapguard batteries.", title_any=("battery",), categories=("Computers",), brands=("Lapguard",)),
    target("SPEEDWAV_SHADE", "Speedwav car sun shade", "a Speedwav screen to reduce sunlight entering a vehicle", "Brand-constrained Speedwav sun shades.", title_any=("sun shade", "sunshade"), categories=("Automotive",), brands=("Speedwav",)),
    target("RAYMOND_SHIRT", "Raymond men's formal shirt", "office clothing for him made by Raymond", "Brand-constrained Raymond shirts.", title_any=("shirt",), categories=("Clothing",), brands=("Raymond",)),
    target("DURIAN_SOFA", "Durian leather sofa", "a leather-covered couch made by Durian", "Brand-constrained Durian sofas.", title_any=("sofa",), categories=("Furniture",), brands=("Durian",)),
    target("WALLMANTRA_STICKER", "Wallmantra wall sticker", "a decorative vinyl graphic from Wallmantra", "Brand-constrained Wallmantra stickers.", title_any=("sticker",), categories=("Home Decor & Festive Needs",), brands=("Wallmantra",)),
    target("KARATCRAFT_RING", "Karatcraft diamond ring", "a diamond finger band made by Karatcraft", "Brand-constrained Karatcraft rings.", title_any=("ring",), categories=("Jewellery",), brands=("Karatcraft",)),
)


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def query_record(index: int, item: dict[str, object], intent_class: str) -> dict[str, object]:
    relevance = dict(item["relevance"])
    hard_filters = {
        key: relevance[key]
        for key in ("categories", "brands", "exclude_terms", "max_price_minor")
        if key in relevance
    }
    groups = [intent_class]
    if "categories" in relevance:
        groups.append("category")
    if "brands" in relevance:
        groups.append("brand-constrained")
    if "max_price_minor" in relevance:
        groups.append("budget-constrained")
    prefix = "L" if intent_class == "literal" else "P"
    return {
        "query_id": f"SV2-{prefix}{index:03d}-{item['key']}",
        "text": item[intent_class],
        "intent_class": intent_class,
        "groups": groups,
        "pair_id": f"SV2-PAIR-{index:03d}",
        "note": item["note"],
        "hard_filters": hard_filters,
        "relevance": relevance,
    }


def main() -> int:
    queries = []
    for index, item in enumerate(TARGETS, start=1):
        queries.append(query_record(index, item, "literal"))
        queries.append(query_record(index, item, "paraphrase"))
    query_set = {
        "schema_version": "semantic-retrieval-eval-v2",
        "catalog_sha256": CATALOG_SHA256,
        "authored_before_model_evaluation": True,
        "outcomes_included": False,
        "relevance_scale": "binary predicate over every catalog document",
        "queries": queries,
    }
    query_bytes = json.dumps(query_set, indent=2, ensure_ascii=False).encode() + b"\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "queries.json").write_bytes(query_bytes)

    generator_sha = sha256(Path(__file__).read_bytes()).hexdigest()
    freeze_payload = {
        "schema_version": "semantic-v2-freeze-v1",
        "status": "FROZEN_BEFORE_MEASUREMENT",
        "base_commit": BASE_COMMIT,
        "catalog_sha256": CATALOG_SHA256,
        "query_artifact": "data/eval/semantic-v2/queries.json",
        "query_artifact_sha256": sha256(query_bytes).hexdigest(),
        "query_count": len(queries),
        "query_pair_count": len(TARGETS),
        "literal_count": sum(q["intent_class"] == "literal" for q in queries),
        "paraphrase_count": sum(q["intent_class"] == "paraphrase" for q in queries),
        "generator": "scripts/freeze_semantic_v2.py",
        "generator_sha256": generator_sha,
        "candidate_cutoff": {"bm25": 100, "dense": 100, "final": 10},
        "fusion_candidates": ["bm25", "dense", "rrf", "weighted_fusion"],
        "metrics": ["recall_at_1", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10"],
        "metric_definitions": {
            "recall_at_k": "relevant hits in top k divided by min(k, total relevant documents)",
            "mrr": "reciprocal rank of the first relevant document; zero when none is retrieved",
            "ndcg_at_10": "binary DCG at 10 divided by ideal binary DCG for min(10, total relevant documents)",
            "aggregation": "unweighted arithmetic mean across queries in each slice",
        },
        "fusion_parameters": {
            "rrf_rank_constant": 60,
            "weighted_bm25": 0.5,
            "weighted_dense": 0.5,
            "weighted_normalization": "min-max independently within each top-100 candidate list; missing candidate score is zero",
            "tie_breaker": "ascending catalog document id",
        },
        "report_slices": ["all", "literal", "paraphrase", "category", "brand-constrained", "budget-constrained"],
        "model_selection_rule": {
            "primary": "highest paraphrase recall_at_10 on this frozen query set",
            "tie_breakers": ["all ndcg_at_10", "full discovery p95 latency", "resident memory", "artifact bytes"],
            "adoption_gate": "enable dense retrieval only for a material frozen-evaluation improvement without unacceptable measured runtime cost",
            "materiality_threshold": "paraphrase recall_at_10 must improve by at least 0.10 absolute over BM25",
            "runtime_thresholds": {
                "warm_full_discovery_p95_ms_max": 250,
                "incremental_resident_memory_bytes_max": 524288000,
                "runtime_external_model_calls_max": 0
            },
            "no_repeated_test_tuning": True,
        },
        "outcomes_included": False,
    }
    freeze = dict(freeze_payload)
    freeze["freeze_payload_sha256"] = sha256(canonical_bytes(freeze_payload)).hexdigest()
    (OUT / "FREEZE.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(queries)} frozen semantic-v2 queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
