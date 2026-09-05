"""Frozen construction vocabulary for the synthetic sandbox commerce world.

Everything a sandbox product is made of lives here as data: the categories, the
merchants that trade in them, the brand and model-name components, the price
bands, the declared purposes, and the sentence templates that become each
merchant's authoritative evidence.

Two properties of this file matter more than its contents.

**It is versioned.** ``WORLD_VERSION`` names the exact vocabulary a generated
universe came from. Change a price band, a template sentence, or the order of a
tuple, and the generated world changes; the version must change with it, and the
frozen manifest test will say so before anything ships.

**It is not a label store.** The evidence sentences below are *claims a merchant
publishes*, not verdicts. The word "gambling" appearing in a syllabus does not
mark that product BLOCK; it gives the controller something true to read, and the
controller reaches its own conclusion. Nothing here is consulted at decision
time, and nothing here can be reached from a decision path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


#: Bump on any change to the vocabulary below. The frozen manifest pins it.
WORLD_VERSION = "mandateguard-sandbox-commerce-v3"

#: Domain-separated generator seed. Fixed; never read from the environment.
WORLD_SEED = 20260904

#: Every sandbox merchant identifier starts with this. Enforced by tests: it is
#: what makes a sandbox identity impossible to confuse with a registered one
#: (``merchant-*``) or a crawled marketplace listing.
SANDBOX_MERCHANT_PREFIX = "sandbox-"

#: Products generated per category. 44 categories -> 3,960 products.
PRODUCTS_PER_CATEGORY = 90

#: Distinct price points spread across each category's band.
PRICE_STEPS = 24

CURRENCY = "INR"


class EvidenceFamily(str, Enum):
    """What a sandbox merchant has actually published about one listing.

    These are world states, not expected outcomes. ``BILLING_UNDECLARED`` says
    the merchant never wrote down a billing model; whether that ends in ALLOW,
    BLOCK or REVIEW depends entirely on what the buyer asked for and what the
    controller makes of it.
    """

    #: A complete, internally consistent record: billing, content, purpose.
    COMPLETE = "EVIDENCE_COMPLETE"
    #: A published, unambiguous recurring subscription.
    RECURRING_DECLARED = "RECURRING_DECLARED"
    #: A syllabus that openly declares excluded content.
    PROHIBITED_CONTENT_DECLARED = "PROHIBITED_CONTENT_DECLARED"
    #: A listing whose billing model and intended use were never recorded.
    BILLING_UNDECLARED = "BILLING_UNDECLARED"
    #: Two current merchant records that contradict each other.
    AUTHORITY_CONFLICT = "AUTHORITY_CONFLICT"


#: Canonical declared purposes. A merchant's "Intended use" sentence is built
#: from this closed vocabulary, and the sandbox intent reader recognises the
#: same phrases, so a stated purpose either matches published evidence or is
#: honestly reported as unestablished.
PURPOSE_INDIVIDUAL_STUDY = "individual study"
PURPOSE_PROFESSIONAL_DEVELOPMENT = "professional development"
PURPOSE_OFFICE_WORK = "office work"
PURPOSE_HOME_USE = "home use"
PURPOSE_PERSONAL_USE = "personal use"
PURPOSE_FITNESS_TRAINING = "fitness training"
PURPOSE_TRAVEL_USE = "travel use"
PURPOSE_PHOTOGRAPHY_WORK = "photography work"
PURPOSE_KITCHEN_USE = "kitchen use"
PURPOSE_GENERAL_USE = "general use"

ALL_PURPOSES: tuple[str, ...] = (
    PURPOSE_INDIVIDUAL_STUDY,
    PURPOSE_PROFESSIONAL_DEVELOPMENT,
    PURPOSE_OFFICE_WORK,
    PURPOSE_HOME_USE,
    PURPOSE_PERSONAL_USE,
    PURPOSE_FITNESS_TRAINING,
    PURPOSE_TRAVEL_USE,
    PURPOSE_PHOTOGRAPHY_WORK,
    PURPOSE_KITCHEN_USE,
    PURPOSE_GENERAL_USE,
)


@dataclass(frozen=True, slots=True)
class Category:
    """One product family and the shape of the listings generated inside it."""

    category_id: str
    label: str
    group: str
    #: Inclusive price band in minor units. Bands deliberately start low so an
    #: ordinary "under 2,000" request finds real candidates rather than a wall.
    price_low_minor: int
    price_high_minor: int
    #: Search vocabulary: what a person might type to mean this category.
    synonyms: tuple[str, ...]
    #: Model-name nouns. Combined with an adjective and a series suffix.
    nouns: tuple[str, ...]
    adjectives: tuple[str, ...]
    #: Declared intended uses published in this category's evidence.
    purposes: tuple[str, ...]
    #: Descriptive tail sentence for the listing description.
    detail: str
    #: True where a recurring billing model is a plausible listing for the
    #: category. Prohibited-content declarations only occur where a syllabus or
    #: a content catalogue is a plausible artefact.
    allows_recurring: bool = False
    allows_prohibited_content: bool = False
    #: Subject matter, for categories where the subject is the thing being
    #: bought. "Course" is not a searchable product; "Finance Course" is.
    subjects: tuple[str, ...] = ()
    #: Per-thousand weight override for declared prohibited content. Education
    #: categories carry more of it than the world average, because a course
    #: catalogue is exactly where a buyer's content exclusion has to be tested.
    prohibited_content_weight: int | None = None


CATEGORIES: tuple[Category, ...] = (
    Category(
        category_id="audio-headphones",
        label="Headphones",
        group="Electronics",
        price_low_minor=89_900,
        price_high_minor=1_499_900,
        synonyms=(
            "headphones", "headphone", "headset", "over ear", "wireless headphones",
            "audio", "cans", "anc",
        ),
        nouns=("Headphones", "Studio Headphones", "Wireless Headset", "Over-Ear Headphones"),
        adjectives=("Wireless", "Noise-Isolating", "Lightweight", "Studio", "Everyday", "Foldable"),
        purposes=(
            PURPOSE_PERSONAL_USE, PURPOSE_HOME_USE, PURPOSE_OFFICE_WORK,
            PURPOSE_INDIVIDUAL_STUDY, PURPOSE_FITNESS_TRAINING, PURPOSE_TRAVEL_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="Padded ear cups, a detachable cable and a folding headband for storage.",
    ),
    Category(
        category_id="audio-earbuds",
        label="Earbuds",
        group="Electronics",
        price_low_minor=69_900,
        price_high_minor=999_900,
        synonyms=("earbuds", "earphones", "tws", "in ear", "buds", "audio", "earpods"),
        nouns=("Earbuds", "Wireless Earbuds", "In-Ear Monitors", "Sport Earbuds"),
        adjectives=("Compact", "Sweat-Resistant", "True Wireless", "Everyday", "Secure-Fit"),
        purposes=(
            PURPOSE_PERSONAL_USE, PURPOSE_FITNESS_TRAINING, PURPOSE_TRAVEL_USE,
            PURPOSE_HOME_USE, PURPOSE_GENERAL_USE,
        ),
        detail="Three silicone tip sizes and a pocket charging case.",
    ),
    Category(
        category_id="audio-speakers",
        label="Speakers",
        group="Electronics",
        price_low_minor=119_900,
        price_high_minor=1_999_900,
        synonyms=("speaker", "speakers", "bluetooth speaker", "soundbar", "audio", "sound"),
        nouns=("Speaker", "Portable Speaker", "Desk Speaker", "Soundbar"),
        adjectives=("Portable", "Splash-Resistant", "Compact", "Room-Filling", "Desk"),
        purposes=(
            PURPOSE_HOME_USE, PURPOSE_PERSONAL_USE, PURPOSE_OFFICE_WORK,
            PURPOSE_GENERAL_USE,
        ),
        detail="A passive radiator, a rubberised base and a USB-C charging port.",
    ),
    Category(
        category_id="computing-laptops",
        label="Laptops",
        group="Electronics",
        price_low_minor=2_899_900,
        price_high_minor=12_999_900,
        synonyms=("laptop", "notebook computer", "ultrabook", "computer", "pc"),
        nouns=("Laptop", "Thin Laptop", "Study Laptop", "Work Laptop"),
        adjectives=("14-inch", "15-inch", "Lightweight", "Long-Battery", "Everyday"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY,
            PURPOSE_PROFESSIONAL_DEVELOPMENT, PURPOSE_HOME_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A backlit keyboard, a matte display panel and two USB-C ports.",
    ),
    Category(
        category_id="computing-laptop-accessories",
        label="Laptop accessories",
        group="Electronics",
        price_low_minor=39_900,
        price_high_minor=699_900,
        synonyms=(
            "laptop stand", "laptop accessories", "docking station", "laptop sleeve",
            "cooling pad", "stand", "riser", "dock",
        ),
        nouns=("Laptop Stand", "Laptop Sleeve", "Docking Station", "Cooling Pad", "Riser Stand"),
        adjectives=("Adjustable", "Aluminium", "Foldable", "Ventilated", "Slim"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY, PURPOSE_HOME_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A hinged aluminium frame with silicone pads and a folding travel profile.",
    ),
    Category(
        category_id="computing-keyboards",
        label="Keyboards",
        group="Electronics",
        price_low_minor=79_900,
        price_high_minor=1_499_900,
        synonyms=("keyboard", "mechanical keyboard", "typing", "keys", "keycaps"),
        nouns=("Keyboard", "Mechanical Keyboard", "Compact Keyboard", "Wireless Keyboard"),
        adjectives=("Mechanical", "Low-Profile", "Tenkeyless", "Hot-Swappable", "Quiet"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY,
            PURPOSE_PROFESSIONAL_DEVELOPMENT, PURPOSE_GENERAL_USE,
        ),
        detail="Tactile switches, a detachable braided cable and PBT keycaps.",
    ),
    Category(
        category_id="computing-mice",
        label="Mice and pointing",
        group="Electronics",
        price_low_minor=39_900,
        price_high_minor=699_900,
        synonyms=("mouse", "mice", "trackpad", "pointing device", "wireless mouse", "trackball"),
        nouns=("Mouse", "Wireless Mouse", "Ergonomic Mouse", "Trackball"),
        adjectives=("Ergonomic", "Silent-Click", "Rechargeable", "Compact"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY, PURPOSE_HOME_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A textured grip, six programmable buttons and a USB-C charge port.",
    ),
    Category(
        category_id="computing-monitors",
        label="Monitors",
        group="Electronics",
        price_low_minor=699_900,
        price_high_minor=4_499_900,
        synonyms=("monitor", "display", "screen", "second screen", "external monitor"),
        nouns=("Monitor", "Display", "Portable Monitor", "Studio Display"),
        adjectives=("24-inch", "27-inch", "Height-Adjustable", "Matte", "Portable"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY,
            PURPOSE_PROFESSIONAL_DEVELOPMENT, PURPOSE_GENERAL_USE,
        ),
        detail="A tilting stand, two HDMI inputs and a matte anti-glare coating.",
    ),
    Category(
        category_id="mobile-accessories",
        label="Mobile accessories",
        group="Electronics",
        price_low_minor=24_900,
        price_high_minor=499_900,
        synonyms=(
            "phone case", "mobile accessories", "screen protector", "phone holder",
            "cable", "car mount", "phone accessories",
        ),
        nouns=("Phone Case", "Screen Protector", "Phone Holder", "Charging Cable", "Car Mount"),
        adjectives=("Shock-Absorbing", "Tempered", "Magnetic", "Braided", "Slim"),
        purposes=(
            PURPOSE_PERSONAL_USE, PURPOSE_TRAVEL_USE, PURPOSE_HOME_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="Raised camera edges, a matte finish and a one-year replacement window.",
    ),
    Category(
        category_id="mobile-power",
        label="Power banks and charging",
        group="Electronics",
        price_low_minor=49_900,
        price_high_minor=599_900,
        synonyms=(
            "power bank", "powerbank", "charger", "fast charger", "battery pack",
            "charging", "adapter",
        ),
        nouns=("Power Bank", "Fast Charger", "Battery Pack", "Wall Adapter"),
        adjectives=("10000mAh", "20000mAh", "Fast-Charge", "Slim", "Dual-Port"),
        purposes=(
            PURPOSE_TRAVEL_USE, PURPOSE_PERSONAL_USE, PURPOSE_OFFICE_WORK,
            PURPOSE_GENERAL_USE,
        ),
        detail="Two output ports, pass-through charging and a four-segment charge indicator.",
    ),
    Category(
        category_id="wearables-smartwatches",
        label="Smartwatches",
        group="Electronics",
        price_low_minor=149_900,
        price_high_minor=2_999_900,
        synonyms=(
            "smartwatch", "smart watch", "fitness watch", "watch", "wearable",
            "fitness tracker", "activity band",
        ),
        nouns=("Smartwatch", "Fitness Watch", "Activity Tracker", "Sport Watch"),
        adjectives=("AMOLED", "Water-Resistant", "GPS", "Lightweight", "Always-On"),
        purposes=(
            PURPOSE_FITNESS_TRAINING, PURPOSE_PERSONAL_USE, PURPOSE_HOME_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A silicone strap, a heart-rate sensor and a seven-day battery estimate.",
    ),
    Category(
        category_id="cameras",
        label="Cameras",
        group="Electronics",
        price_low_minor=899_900,
        price_high_minor=9_999_900,
        synonyms=("camera", "dslr", "mirrorless", "vlogging camera", "photography", "shooter"),
        nouns=("Mirrorless Camera", "Compact Camera", "Vlogging Camera", "Camera Body"),
        adjectives=("Entry-Level", "Beginner", "Compact", "Weather-Sealed", "24MP"),
        purposes=(
            PURPOSE_PHOTOGRAPHY_WORK, PURPOSE_PROFESSIONAL_DEVELOPMENT,
            PURPOSE_PERSONAL_USE, PURPOSE_TRAVEL_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A tilting screen, in-body stabilisation and a bundled kit lens.",
    ),
    Category(
        category_id="camera-accessories",
        label="Photography accessories",
        group="Electronics",
        price_low_minor=49_900,
        price_high_minor=1_299_900,
        synonyms=(
            "tripod", "camera bag", "lens filter", "photography accessories", "gimbal",
            "photography", "light panel",
        ),
        nouns=("Tripod", "Camera Bag", "Lens Filter Set", "Gimbal", "Light Panel"),
        adjectives=("Carbon", "Travel", "Adjustable", "Padded", "Compact"),
        purposes=(
            PURPOSE_PHOTOGRAPHY_WORK, PURPOSE_TRAVEL_USE, PURPOSE_PERSONAL_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A quick-release plate, rubber feet and a carry pouch.",
    ),
    Category(
        category_id="lighting-desk-lamps",
        label="Desk lamps",
        group="Home",
        price_low_minor=39_900,
        price_high_minor=499_900,
        synonyms=(
            "desk lamp", "study lamp", "table lamp", "reading lamp", "lamp",
            "study light", "night light", "task light",
        ),
        nouns=("Desk Lamp", "Study Lamp", "Reading Lamp", "Task Lamp", "Clamp Lamp"),
        adjectives=("Dimmable", "Warm-White", "Adjustable", "Eye-Care", "Rechargeable"),
        purposes=(
            PURPOSE_INDIVIDUAL_STUDY, PURPOSE_OFFICE_WORK, PURPOSE_HOME_USE,
            PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A weighted base, a three-step brightness control and a flicker-free driver.",
    ),
    Category(
        category_id="lighting-home",
        label="Home lighting",
        group="Home",
        price_low_minor=29_900,
        price_high_minor=799_900,
        synonyms=(
            "home lighting", "ceiling light", "floor lamp", "led strip", "bulb",
            "lighting", "sconce",
        ),
        nouns=("Floor Lamp", "LED Strip", "Ceiling Light", "Wall Sconce", "Bulb Set"),
        adjectives=("Warm-White", "Dimmable", "Adhesive", "Minimal", "Energy-Saving"),
        purposes=(PURPOSE_HOME_USE, PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE),
        detail="A two-metre cable, an inline switch and a five-year LED rating.",
    ),
    Category(
        category_id="furniture-office-chairs",
        label="Office chairs",
        group="Furniture",
        price_low_minor=349_900,
        price_high_minor=3_499_900,
        synonyms=("office chair", "desk chair", "ergonomic chair", "chair", "study chair", "seating"),
        nouns=("Office Chair", "Task Chair", "Ergonomic Chair", "Study Chair"),
        adjectives=("Mesh-Back", "High-Back", "Ergonomic", "Adjustable", "Lumbar-Support"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY, PURPOSE_HOME_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A gas-lift column, nylon castors and a three-position tilt lock.",
    ),
    Category(
        category_id="furniture-desks",
        label="Desks",
        group="Furniture",
        price_low_minor=499_900,
        price_high_minor=3_999_900,
        synonyms=("desk", "study table", "work table", "standing desk", "computer table", "workstation"),
        nouns=("Desk", "Study Table", "Standing Desk", "Writing Desk"),
        adjectives=("Compact", "Height-Adjustable", "Engineered-Wood", "Two-Drawer", "Corner"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY, PURPOSE_HOME_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A scratch-resistant top, a cable cut-out and levelling feet.",
    ),
    Category(
        category_id="furniture-storage",
        label="Storage furniture",
        group="Furniture",
        price_low_minor=129_900,
        price_high_minor=1_999_900,
        synonyms=("bookshelf", "shelf", "storage", "cabinet", "drawer unit", "rack", "shelving"),
        nouns=("Bookshelf", "Storage Rack", "Drawer Unit", "Shoe Cabinet"),
        adjectives=("Five-Tier", "Compact", "Wall-Mounted", "Open-Back", "Stackable"),
        purposes=(PURPOSE_HOME_USE, PURPOSE_OFFICE_WORK, PURPOSE_GENERAL_USE),
        detail="Powder-coated steel uprights, engineered-wood shelves and anti-tip hardware.",
    ),
    Category(
        category_id="office-accessories",
        label="Office accessories",
        group="Office",
        price_low_minor=19_900,
        price_high_minor=499_900,
        synonyms=(
            "office accessories", "desk organiser", "desk organizer", "monitor riser",
            "footrest", "whiteboard", "cable tray",
        ),
        nouns=("Desk Organiser", "Monitor Riser", "Footrest", "Whiteboard", "Cable Tray"),
        adjectives=("Bamboo", "Compact", "Adjustable", "Magnetic", "Under-Desk"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY, PURPOSE_HOME_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A felt-lined tray, non-slip feet and a flat-pack assembly.",
    ),
    Category(
        category_id="stationery",
        label="Stationery",
        group="Office",
        price_low_minor=9_900,
        price_high_minor=249_900,
        synonyms=(
            "stationery", "notebook", "pens", "notepad", "diary", "planner",
            "highlighter", "writing",
        ),
        nouns=("Notebook Set", "Gel Pen Set", "Planner", "Sticky Note Pack", "Highlighter Set"),
        adjectives=("Dotted", "Hardbound", "Refillable", "A5", "Recycled-Paper"),
        purposes=(
            PURPOSE_INDIVIDUAL_STUDY, PURPOSE_OFFICE_WORK, PURPOSE_PERSONAL_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="Ninety-gram paper, a lay-flat binding and a ribbon marker.",
    ),
    Category(
        category_id="books",
        label="Books",
        group="Media",
        price_low_minor=19_900,
        price_high_minor=299_900,
        synonyms=("book", "books", "paperback", "reading", "guide", "textbook", "handbook"),
        nouns=("Paperback", "Field Guide", "Reference Handbook", "Workbook"),
        adjectives=("Illustrated", "Second-Edition", "Beginner", "Practical", "Annotated"),
        purposes=(
            PURPOSE_INDIVIDUAL_STUDY, PURPOSE_PROFESSIONAL_DEVELOPMENT,
            PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A stitched paperback binding with a subject index and further reading.",
        allows_prohibited_content=True,
        subjects=(
            "Finance", "Photography", "Cooking", "History", "Data Analysis",
            "Gardening", "Marketing", "Public Speaking", "Mathematics", "Travel",
            "Personal Finance", "Design", "Statistics", "Writing",
        ),
        prohibited_content_weight=110,
    ),
    Category(
        category_id="bags-backpacks",
        label="Backpacks",
        group="Lifestyle",
        price_low_minor=69_900,
        price_high_minor=899_900,
        synonyms=("backpack", "bag", "rucksack", "college bag", "laptop bag", "daypack", "school bag"),
        nouns=("Backpack", "Laptop Backpack", "Daypack", "College Backpack"),
        adjectives=("Water-Resistant", "25-Litre", "Padded", "Anti-Theft", "Lightweight"),
        purposes=(
            PURPOSE_TRAVEL_USE, PURPOSE_INDIVIDUAL_STUDY, PURPOSE_OFFICE_WORK,
            PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A padded laptop sleeve, a chest strap and a rain cover.",
    ),
    Category(
        category_id="bags-luggage",
        label="Luggage",
        group="Lifestyle",
        price_low_minor=199_900,
        price_high_minor=1_999_900,
        synonyms=("luggage", "suitcase", "trolley bag", "cabin bag", "duffel", "travel bag"),
        nouns=("Cabin Trolley", "Check-In Suitcase", "Duffel Bag", "Travel Case"),
        adjectives=("Hardshell", "Four-Wheel", "Expandable", "Lightweight", "TSA-Lock"),
        purposes=(PURPOSE_TRAVEL_USE, PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE),
        detail="A telescopic handle, spinner wheels and a fabric divider.",
    ),
    Category(
        category_id="footwear-running",
        label="Running shoes",
        group="Sportswear",
        price_low_minor=129_900,
        price_high_minor=1_499_900,
        synonyms=(
            "running shoes", "running shoe", "runners", "trainers", "jogging shoes",
            "shoes", "sports shoes", "running",
        ),
        nouns=("Running Shoes", "Road Running Shoes", "Trail Running Shoes", "Training Shoes"),
        adjectives=("Cushioned", "Breathable", "Lightweight", "Neutral", "Stability"),
        purposes=(PURPOSE_FITNESS_TRAINING, PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE),
        detail="A knit upper, a foam midsole and a rubber outsole with a flex groove.",
    ),
    Category(
        category_id="footwear-casual",
        label="Casual shoes",
        group="Sportswear",
        price_low_minor=99_900,
        price_high_minor=999_900,
        synonyms=("casual shoes", "sneakers", "loafers", "slip ons", "shoes", "walking shoes", "canvas shoes"),
        nouns=("Sneakers", "Canvas Shoes", "Walking Shoes", "Slip-Ons"),
        adjectives=("Everyday", "Canvas", "Cushioned", "Low-Top", "Breathable"),
        purposes=(PURPOSE_PERSONAL_USE, PURPOSE_TRAVEL_USE, PURPOSE_GENERAL_USE),
        detail="A padded collar, a textile lining and a cemented rubber sole.",
    ),
    Category(
        category_id="fitness-equipment",
        label="Fitness equipment",
        group="Sports",
        price_low_minor=49_900,
        # Entry-level mats, bands and small weights should be useful to a
        # person asking for ordinary home-gym gear below INR 2,000-5,000. A
        # wider band made every generated yoga mat miss that common budget and
        # left a kettlebell as the first merely-related result.
        price_high_minor=499_900,
        synonyms=(
            "fitness", "gym", "dumbbell", "yoga mat", "resistance band", "workout",
            "exercise", "home gym", "kettlebell",
        ),
        nouns=("Yoga Mat", "Dumbbell Set", "Resistance Band Set", "Skipping Rope", "Kettlebell"),
        adjectives=("Non-Slip", "Adjustable", "Neoprene", "Home-Gym", "6mm"),
        purposes=(
            PURPOSE_FITNESS_TRAINING, PURPOSE_HOME_USE, PURPOSE_PERSONAL_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A textured grip surface, a carry strap and a moisture-resistant finish.",
    ),
    Category(
        category_id="sports-equipment",
        label="Sports equipment",
        group="Sports",
        price_low_minor=39_900,
        price_high_minor=1_999_900,
        synonyms=(
            "sports equipment", "cricket bat", "badminton racket", "football",
            "sports gear", "racquet", "table tennis",
        ),
        nouns=("Badminton Racket", "Cricket Bat", "Football", "Table Tennis Set", "Shuttlecock Pack"),
        adjectives=("Tournament", "Practice", "Graphite", "Junior", "All-Weather"),
        purposes=(PURPOSE_FITNESS_TRAINING, PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE),
        detail="A taped grip, a protective cover and a stitched seam finish.",
    ),
    Category(
        category_id="kitchen-tools",
        label="Kitchen tools",
        group="Home",
        price_low_minor=19_900,
        price_high_minor=599_900,
        synonyms=(
            "kitchen", "cookware", "knife", "chopping board", "storage jar", "pan",
            "utensils", "cooking",
        ),
        nouns=("Chef Knife", "Chopping Board", "Storage Jar Set", "Frying Pan", "Measuring Set"),
        adjectives=("Stainless", "Non-Stick", "Bamboo", "Airtight", "Induction-Ready"),
        purposes=(
            PURPOSE_KITCHEN_USE, PURPOSE_HOME_USE, PURPOSE_PERSONAL_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A riveted handle, a dishwasher-safe finish and a two-year warranty.",
    ),
    Category(
        category_id="home-appliances",
        label="Home appliances",
        group="Home",
        price_low_minor=149_900,
        price_high_minor=3_999_900,
        synonyms=(
            "appliance", "appliances", "kettle", "mixer", "air fryer", "vacuum",
            "iron", "grinder",
        ),
        nouns=("Electric Kettle", "Mixer Grinder", "Air Fryer", "Vacuum Cleaner", "Steam Iron"),
        adjectives=("1.5-Litre", "Compact", "Quiet", "Energy-Rated", "Auto-Shutoff"),
        purposes=(
            PURPOSE_HOME_USE, PURPOSE_KITCHEN_USE, PURPOSE_PERSONAL_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="A concealed heating element, a cool-touch body and a two-year warranty.",
    ),
    Category(
        category_id="drinkware-water-bottles",
        label="Water bottles and drinkware",
        group="Home",
        price_low_minor=29_900,
        price_high_minor=249_900,
        synonyms=(
            "water bottle", "water bottles", "bottle", "drinkware", "flask",
            "insulated flask", "tumbler", "sports bottle",
        ),
        nouns=("Water Bottle", "Insulated Flask", "Travel Tumbler", "Sports Bottle"),
        adjectives=("Leakproof", "Insulated", "Stainless-Steel", "Lightweight", "Wide-Mouth"),
        purposes=(
            PURPOSE_PERSONAL_USE, PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY,
            PURPOSE_FITNESS_TRAINING, PURPOSE_TRAVEL_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A leak-resistant lid, a carry loop and a food-safe inner surface.",
    ),
    Category(
        category_id="apparel-basics",
        label="Apparel basics",
        group="Apparel",
        price_low_minor=39_900,
        price_high_minor=299_900,
        synonyms=(
            "apparel", "clothing", "t shirt", "t shirts", "tee", "shirt",
            "hoodie", "joggers", "everyday wear",
        ),
        nouns=("T-Shirt", "Everyday Shirt", "Pullover Hoodie", "Joggers"),
        adjectives=("Cotton", "Relaxed-Fit", "Everyday", "Lightweight", "Soft-Knit"),
        purposes=(PURPOSE_PERSONAL_USE, PURPOSE_HOME_USE, PURPOSE_TRAVEL_USE, PURPOSE_GENERAL_USE),
        detail="A machine-washable fabric, reinforced seams and a printed care label.",
    ),
    Category(
        category_id="apparel-jackets",
        label="Jackets and outerwear",
        group="Apparel",
        price_low_minor=99_900,
        price_high_minor=799_900,
        synonyms=(
            "jacket", "jackets", "winter jacket", "rain jacket", "raincoat",
            "coat", "outerwear", "windcheater",
        ),
        nouns=("Winter Jacket", "Rain Jacket", "Everyday Coat", "Windcheater"),
        adjectives=("Warm-Lined", "Water-Resistant", "Packable", "Lightweight", "Insulated"),
        purposes=(PURPOSE_PERSONAL_USE, PURPOSE_TRAVEL_USE, PURPOSE_GENERAL_USE),
        detail="A full-length zip, two secured pockets and a clearly labelled shell fabric.",
    ),
    Category(
        category_id="home-textiles",
        label="Home textiles and bedsheets",
        group="Home",
        price_low_minor=49_900,
        price_high_minor=599_900,
        synonyms=(
            "bedsheet", "bedsheets", "bed sheet", "bed linen", "duvet cover",
            "blanket", "pillowcase", "home textiles", "bedding",
        ),
        nouns=("Bedsheet Set", "Duvet Cover", "Bed Blanket", "Pillowcase Set"),
        adjectives=("Cotton", "Queen-Size", "Double-Bed", "Soft-Woven", "Easy-Care"),
        purposes=(PURPOSE_HOME_USE, PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE),
        detail="A labelled fibre composition, colourfast finish and machine-washable care instructions.",
    ),
    Category(
        category_id="printers",
        label="Printers",
        group="Electronics",
        price_low_minor=399_900,
        price_high_minor=2_999_900,
        synonyms=(
            "printer", "printers", "inkjet printer", "laser printer", "photo printer",
            "multifunction printer", "print documents", "printing",
        ),
        nouns=("Inkjet Printer", "Laser Printer", "Multifunction Printer", "Photo Printer"),
        adjectives=("Wireless", "Compact", "Duplex", "Colour", "Home-Office"),
        purposes=(
            PURPOSE_HOME_USE, PURPOSE_OFFICE_WORK, PURPOSE_INDIVIDUAL_STUDY,
            PURPOSE_GENERAL_USE,
        ),
        detail="Wi-Fi printing, a documented cartridge family and a one-year service warranty.",
    ),
    Category(
        category_id="computer-storage",
        label="Computer storage",
        group="Electronics",
        price_low_minor=149_900,
        price_high_minor=1_999_900,
        synonyms=(
            "hard disk", "hard drive", "external drive", "external hard drive",
            "portable ssd", "ssd", "usb drive", "flash drive", "computer storage",
            "storage drive", "backup photos",
        ),
        nouns=("External Hard Drive", "Portable SSD", "USB Flash Drive", "Storage Drive"),
        adjectives=("Portable", "Rugged", "High-Speed", "Compact", "Encrypted"),
        purposes=(
            PURPOSE_PERSONAL_USE, PURPOSE_OFFICE_WORK, PURPOSE_PHOTOGRAPHY_WORK,
            PURPOSE_TRAVEL_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A stated capacity, bundled cable and documented filesystem compatibility.",
    ),
    Category(
        category_id="coffee-makers",
        label="Coffee makers",
        group="Home",
        price_low_minor=129_900,
        price_high_minor=1_499_900,
        synonyms=(
            "coffee maker", "coffee makers", "coffee machine", "drip coffee",
            "espresso maker", "coffee brewer", "filter coffee machine",
        ),
        nouns=("Coffee Maker", "Drip Coffee Machine", "Espresso Maker", "Coffee Brewer"),
        adjectives=("Compact", "Two-Cup", "Programmable", "Auto-Shutoff", "Glass-Carafe"),
        purposes=(PURPOSE_KITCHEN_USE, PURPOSE_HOME_USE, PURPOSE_OFFICE_WORK, PURPOSE_GENERAL_USE),
        detail="A removable filter basket, measured water tank and automatic shutoff.",
    ),
    Category(
        category_id="cleaning-products",
        label="Cleaning and home care",
        group="Home",
        price_low_minor=9_900,
        price_high_minor=249_900,
        synonyms=(
            "floor cleaner", "floor cleaning", "clean floor", "cleaning product",
            "cleaning products", "surface cleaner", "bathroom cleaner", "home care",
            "mop", "clean my room",
        ),
        nouns=("Floor Cleaner", "Surface Cleaner", "Bathroom Cleaner", "Microfibre Mop Set"),
        adjectives=("Tile-Safe", "Low-Foam", "Concentrated", "Everyday", "Citrus"),
        purposes=(PURPOSE_HOME_USE, PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE),
        detail="A sealed container, measured-use directions and a clearly printed ingredient label.",
    ),
    Category(
        category_id="toys-board-games",
        label="Toys and board games",
        group="Leisure",
        price_low_minor=39_900,
        price_high_minor=499_900,
        synonyms=(
            "board game", "board games", "tabletop game", "strategy game",
            "family game", "card game", "toys", "game night",
        ),
        nouns=("Family Board Game", "Strategy Board Game", "Tabletop Game", "Card Game Set"),
        adjectives=("Four-Player", "Cooperative", "Quick-Play", "Classic", "Travel-Size"),
        purposes=(PURPOSE_HOME_USE, PURPOSE_PERSONAL_USE, PURPOSE_TRAVEL_USE, PURPOSE_GENERAL_USE),
        detail="A printed rulebook, counted playing pieces and a stated player-age range.",
    ),
    Category(
        category_id="travel-accessories",
        label="Travel accessories",
        group="Lifestyle",
        price_low_minor=29_900,
        price_high_minor=399_900,
        synonyms=(
            "travel accessories", "packing cubes", "packing cube", "neck pillow",
            "travel pillow", "luggage organiser", "luggage organizer", "passport holder",
        ),
        nouns=("Packing Cube Set", "Travel Neck Pillow", "Luggage Organiser", "Passport Holder"),
        adjectives=("Packable", "Washable", "Compression", "Memory-Foam", "Lightweight"),
        purposes=(PURPOSE_TRAVEL_USE, PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE),
        detail="A washable cover, labelled dimensions and a compact storage pouch.",
    ),
    Category(
        category_id="personal-care",
        label="Personal care",
        group="Personal",
        price_low_minor=29_900,
        price_high_minor=799_900,
        synonyms=("personal care", "grooming", "trimmer", "hair dryer", "skincare", "shaver", "clipper"),
        nouns=("Beard Trimmer", "Hair Dryer", "Electric Shaver", "Facial Cleanser Device"),
        adjectives=("Cordless", "Rechargeable", "Travel", "Quiet", "Skin-Safe"),
        purposes=(
            PURPOSE_PERSONAL_USE, PURPOSE_HOME_USE, PURPOSE_TRAVEL_USE,
            PURPOSE_GENERAL_USE,
        ),
        detail="Washable heads, a charging stand and a sixty-minute runtime.",
    ),
    Category(
        category_id="education-courses",
        label="Professional courses",
        group="Education",
        price_low_minor=99_900,
        price_high_minor=1_999_900,
        synonyms=(
            "course", "courses", "training", "certification", "finance course",
            "photography course", "class", "workshop", "masterclass",
        ),
        nouns=("Course", "Certificate Course", "Workshop", "Masterclass"),
        adjectives=("Beginner", "Self-Paced", "Practical", "Foundation", "Advanced"),
        purposes=(
            PURPOSE_PROFESSIONAL_DEVELOPMENT, PURPOSE_INDIVIDUAL_STUDY,
            PURPOSE_GENERAL_USE,
        ),
        detail="Recorded modules, downloadable worksheets and a completion certificate.",
        allows_prohibited_content=True,
        subjects=(
            "Finance", "Personal Finance", "Photography", "Data Analysis",
            "Project Management", "Marketing", "Public Speaking", "Accounting",
            "Product Design", "Negotiation", "Investing", "Spreadsheet Modelling",
            "Video Editing", "Business Writing",
        ),
        prohibited_content_weight=200,
    ),
    Category(
        category_id="education-tutoring",
        label="Education services",
        group="Education",
        price_low_minor=79_900,
        price_high_minor=1_499_900,
        synonyms=("tutoring", "coaching", "lessons", "tuition", "study help", "exam prep", "tutor"),
        nouns=("Tutoring Pack", "Exam Prep Pack", "Coaching Session Set", "Study Programme"),
        adjectives=("Six-Session", "Foundation", "Weekend", "One-to-One", "Group"),
        purposes=(
            PURPOSE_INDIVIDUAL_STUDY, PURPOSE_PROFESSIONAL_DEVELOPMENT,
            PURPOSE_GENERAL_USE,
        ),
        detail="Scheduled sessions, practice sets and written feedback after each session.",
        allows_recurring=True,
        allows_prohibited_content=True,
        subjects=(
            "Mathematics", "Physics", "Finance", "English", "Chemistry",
            "Economics", "Statistics", "Computer Science", "Biology", "Accountancy",
        ),
        prohibited_content_weight=90,
    ),
    Category(
        category_id="subscriptions-media",
        label="Subscriptions",
        group="Services",
        price_low_minor=19_900,
        price_high_minor=299_900,
        synonyms=(
            "subscription", "subscriptions", "membership", "plan", "monthly plan",
            "streaming", "club",
        ),
        nouns=("Streaming Plan", "Reading Membership", "Audio Membership", "Club Membership"),
        adjectives=("Monthly", "Annual", "Family", "Individual", "Student"),
        purposes=(PURPOSE_PERSONAL_USE, PURPOSE_HOME_USE, PURPOSE_GENERAL_USE),
        detail="Ongoing access to the catalogue for the duration of the billing term.",
        allows_recurring=True,
    ),
    Category(
        category_id="software-services",
        label="Software and services",
        group="Services",
        price_low_minor=29_900,
        price_high_minor=999_900,
        synonyms=("software", "app", "licence", "license", "saas", "tool", "productivity", "backup"),
        nouns=("Software Licence", "Productivity App", "Backup Service", "Design Tool"),
        adjectives=("Single-Seat", "Team", "Desktop", "Cloud", "Offline"),
        purposes=(
            PURPOSE_OFFICE_WORK, PURPOSE_PROFESSIONAL_DEVELOPMENT,
            PURPOSE_PERSONAL_USE, PURPOSE_GENERAL_USE,
        ),
        detail="A signed installer, release notes and twelve months of updates.",
        allows_recurring=True,
        allows_prohibited_content=True,
    ),
)


@dataclass(frozen=True, slots=True)
class Merchant:
    """One synthetic seller of record."""

    merchant_id: str
    display_name: str
    categories: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.merchant_id.startswith(SANDBOX_MERCHANT_PREFIX):
            raise ValueError("sandbox merchant identifiers must carry the prefix")


def _m(slug: str, name: str, *categories: str) -> Merchant:
    return Merchant(
        merchant_id=f"{SANDBOX_MERCHANT_PREFIX}{slug}",
        display_name=name,
        categories=categories,
    )


#: Every merchant is fictional. The names below are constructed for the sandbox
#: and are not intended to refer to any real seller, brand, or company.
MERCHANTS: tuple[Merchant, ...] = (
    _m("relay-audio", "Relay Audio (Synthetic)", "audio-headphones", "audio-earbuds"),
    _m("northwind-sound", "Northwind Sound (Synthetic)", "audio-headphones", "audio-speakers"),
    _m("clearnote-audio", "Clearnote Audio (Synthetic)", "audio-earbuds", "audio-speakers"),
    _m("pinewood-compute", "Pinewood Compute (Synthetic)", "computing-laptops", "computing-monitors"),
    _m("harbour-systems", "Harbour Systems (Synthetic)", "computing-laptops", "computing-laptop-accessories"),
    _m("deskline-supply", "Deskline Supply (Synthetic)", "computing-laptop-accessories", "office-accessories"),
    _m("keycraft-labs", "Keycraft Labs (Synthetic)", "computing-keyboards", "computing-mice"),
    _m("orbit-peripherals", "Orbit Peripherals (Synthetic)", "computing-keyboards", "computing-mice"),
    _m("meridian-display", "Meridian Display (Synthetic)", "computing-monitors", "computing-laptop-accessories"),
    _m("cellworks-retail", "Cellworks Retail (Synthetic)", "mobile-accessories", "mobile-power"),
    _m("voltbay-power", "Voltbay Power (Synthetic)", "mobile-power", "mobile-accessories"),
    _m("tempo-wearables", "Tempo Wearables (Synthetic)", "wearables-smartwatches", "fitness-equipment"),
    _m("summit-wear", "Summit Wear (Synthetic)", "wearables-smartwatches", "footwear-running"),
    _m("aperture-optics", "Aperture Optics (Synthetic)", "cameras", "camera-accessories"),
    _m("shutterline-gear", "Shutterline Gear (Synthetic)", "camera-accessories", "cameras"),
    _m("lumenfield-studio", "Lumenfield Studio (Synthetic)", "lighting-desk-lamps", "lighting-home"),
    _m("brightleaf-lighting", "Brightleaf Lighting (Synthetic)", "lighting-desk-lamps", "lighting-home"),
    _m("glowpath-home", "Glowpath Home (Synthetic)", "lighting-home", "home-appliances"),
    _m("atlas-workspace", "Atlas Workspace (Synthetic)", "furniture-office-chairs", "furniture-desks"),
    _m("oakline-furniture", "Oakline Furniture (Synthetic)", "furniture-desks", "furniture-storage"),
    _m("stackwell-storage", "Stackwell Storage (Synthetic)", "furniture-storage", "office-accessories"),
    _m("quilltop-office", "Quilltop Office (Synthetic)", "office-accessories", "stationery"),
    _m("paperfold-stationery", "Paperfold Stationery (Synthetic)", "stationery", "books"),
    _m("marginalia-books", "Marginalia Books (Synthetic)", "books", "education-courses"),
    _m("trailhead-bags", "Trailhead Bags (Synthetic)", "bags-backpacks", "bags-luggage"),
    _m("wayfare-luggage", "Wayfare Luggage (Synthetic)", "bags-luggage", "bags-backpacks"),
    _m("stride-athletics", "Stride Athletics (Synthetic)", "footwear-running", "footwear-casual"),
    _m("everyday-footwear", "Everyday Footwear (Synthetic)", "footwear-casual", "footwear-running"),
    _m("ironhouse-fitness", "Ironhouse Fitness (Synthetic)", "fitness-equipment", "sports-equipment"),
    _m("courtside-sports", "Courtside Sports (Synthetic)", "sports-equipment", "fitness-equipment"),
    _m("copperpot-kitchen", "Copperpot Kitchen (Synthetic)", "kitchen-tools", "home-appliances"),
    _m("hearth-appliances", "Hearth Appliances (Synthetic)", "home-appliances", "kitchen-tools"),
    _m("verdance-care", "Verdance Care (Synthetic)", "personal-care", "home-appliances"),
    _m("silverbirch-grooming", "Silverbirch Grooming (Synthetic)", "personal-care", "mobile-accessories"),
    _m("veritas-academy-sandbox", "Veritas Academy Sandbox (Synthetic)", "education-courses", "education-tutoring"),
    _m("cornerstone-learning", "Cornerstone Learning (Synthetic)", "education-courses", "education-tutoring"),
    _m("lanternway-tutors", "Lanternway Tutors (Synthetic)", "education-tutoring", "books"),
    _m("readwell-club", "Readwell Club (Synthetic)", "subscriptions-media", "books"),
    _m("streamfield-media", "Streamfield Media (Synthetic)", "subscriptions-media", "software-services"),
    _m("bytecrest-software", "Bytecrest Software (Synthetic)", "software-services", "subscriptions-media"),
    _m("planarworks-tools", "Planarworks Tools (Synthetic)", "software-services", "computing-laptop-accessories"),
    _m("bluepeak-supply", "Bluepeak Supply (Synthetic)", "mobile-accessories", "office-accessories"),
    _m("granary-goods", "Granary Goods (Synthetic)", "kitchen-tools", "stationery"),
    _m("northgate-outfitters", "Northgate Outfitters (Synthetic)", "bags-backpacks", "footwear-casual"),
    _m("halcyon-home", "Halcyon Home (Synthetic)", "lighting-home", "furniture-storage"),
    _m("crescent-optics", "Crescent Optics (Synthetic)", "cameras", "computing-monitors"),
    _m("pace-athletics", "Pace Athletics (Synthetic)", "footwear-running", "fitness-equipment"),
    _m("beacon-workspace", "Beacon Workspace (Synthetic)", "furniture-office-chairs", "office-accessories"),
    _m("tidepool-bags", "Tidepool Bags (Synthetic)", "bags-backpacks", "camera-accessories"),
    _m("greenmill-kitchen", "Greenmill Kitchen (Synthetic)", "kitchen-tools", "personal-care"),
    _m("riverglass-ware", "Riverglass Ware (Synthetic)", "drinkware-water-bottles", "travel-accessories"),
    _m("cottonwood-cloth", "Cottonwood Cloth (Synthetic)", "apparel-basics", "apparel-jackets"),
    _m("stormline-apparel", "Stormline Apparel (Synthetic)", "apparel-jackets", "apparel-basics"),
    _m("loomhouse-home", "Loomhouse Home (Synthetic)", "home-textiles", "cleaning-products"),
    _m("printfield-systems", "Printfield Systems (Synthetic)", "printers", "computer-storage"),
    _m("archive-compute", "Archive Compute (Synthetic)", "computer-storage", "printers"),
    _m("morning-kitchen", "Morning Kitchen (Synthetic)", "coffee-makers", "kitchen-tools"),
    _m("tableturn-games", "Tableturn Games (Synthetic)", "toys-board-games", "books"),
    _m("clearhome-supply", "Clearhome Supply (Synthetic)", "cleaning-products", "home-textiles"),
    _m("waypoint-travel", "Waypoint Travel (Synthetic)", "travel-accessories", "bags-luggage"),
)


#: Brand names are synthetic. They exist so brand-preference search has
#: something real to match, not to evoke any actual manufacturer.
BRANDS: tuple[str, ...] = (
    "Alcova", "Bricklane", "Corvus", "Duneside", "Ellery", "Fernway", "Glasson",
    "Halten", "Ironvale", "Juniper", "Kestrel", "Lowfield", "Mirren",
    "Norlake", "Ostara", "Pellin", "Quarry", "Riverton", "Selwyn",
    "Thorne", "Umbral", "Verity", "Westmoor", "Yarrow", "Zenner",
)

#: Series suffixes give a model line a plausible shape without a real one.
SERIES: tuple[str, ...] = (
    "S1", "S2", "S3", "M40", "M60", "M80", "Pro", "Lite", "Core", "Studio",
    "Classic", "Edge", "One", "Duo", "Plus", "Air",
)

#: Stable judge-facing listings whose exact transaction identity is useful to
#: demonstrate post-authorization binding. They are ordinary members of the
#: generated world with ordinary merchant evidence; this data influences no
#: authorization outcome. Tuple value: ``(sku, price_minor, evidence_family)``.
FEATURED_PRODUCTS: dict[tuple[str, int], tuple[str, int, EvidenceFamily]] = {
    ("audio-headphones", 42): ("headphones-042", 349_900, EvidenceFamily.COMPLETE),
}


# ---------------------------------------------------------------------------
# Evidence sentence templates
#
# The exclusion clause below is the only part of this file with a mechanical
# requirement attached to its wording. The semantic verifier reads a prohibited
# characteristic as *absent* only when the merchant's own text names it and
# negates it. Naming the excluded categories explicitly is therefore not
# padding: it is the difference between a merchant that has published a content
# classification and a merchant that has published nothing and must be reviewed.
# ---------------------------------------------------------------------------

MERCHANT_TERMS_TEMPLATE = (
    "{display_name} is a simulated merchant in the MandateGuard sandbox. It is not a "
    "real seller and no real money moves through it. Each listing published by this "
    "merchant carries its own registered billing statement, its own registered content "
    "classification and its own registered statement of intended use. Where a listing "
    "record and this merchant record disagree, neither is treated as resolved."
)

BILLING_ONE_TIME = (
    "Billing model: one-time purchase, settled once at checkout. This listing has no "
    "subscription, no recurring charge, no membership, no monthly plan, no annual plan, "
    "no renewal, no auto-renewal, no auto-debit, no instalment and no trial conversion."
)

BILLING_RECURRING = (
    "Billing model: a recurring subscription. The registered plan renews automatically "
    "at the end of every billing term and continues to charge the registered instrument "
    "until the buyer cancels it."
)

BILLING_UNDECLARED_SENTENCE = (
    "Billing statement: this merchant has not recorded an applicable billing model for "
    "this listing. Neither a settled-once statement nor a renewing statement is on file."
)

BILLING_CONFLICT_SENTENCE = (
    "Registered billing statement A for this listing records a renewing charge. "
    "Registered billing statement B for this listing records a single settlement. Both "
    "records are current, the two registered records disagree, and no authoritative "
    "billing model is resolved for this listing."
)

CONTENT_CLEAR = (
    "Content classification: this listing contains no gambling, no betting, no wagering, "
    "no casino material, no lottery material, no alcohol, no tobacco, no vaping, no "
    "weapons, no adult material, no crypto trading content, no loan offer and no "
    "political advertising."
)

CONTENT_PROHIBITED = (
    "Content classification: gambling PRESENT. The registered syllabus teaches casino "
    "gambling technique, betting systems, odds pricing and wager selection through "
    "simulated play, and the merchant has recorded this material as present."
)

CONTENT_UNDECLARED_SENTENCE = (
    "Content classification: this merchant has not recorded a content classification for "
    "this listing."
)

PURPOSE_TEMPLATE = "Intended use: {purposes}."

PURPOSE_UNDECLARED_SENTENCE = (
    "Intended use: this merchant has not recorded a documented intended use for this "
    "listing."
)

IDENTITY_TEMPLATE = (
    "Merchant of record: {display_name}, sandbox merchant identifier {merchant_id}. "
    "SKU ownership: {sku} is registered to this merchant. Authoritative price: "
    "{price_text} {currency}, effective {effective_from}. Evidence version {version}."
)

SYNTHETIC_NOTICE = (
    "SYNTHETIC SANDBOX RECORD. This merchant, this listing and this evidence were "
    "generated by MandateGuard for demonstration. They describe no real product, no "
    "real seller and no real transaction."
)
