"""Builds the spike's documents: a made-up outdoor shop's policies, catalogue and product pages.

    python corpus.py <directory>

What a "Customer email replies" solution's buyer would give it: the refund policy (Word), the
shipping page (HTML), the warranty (PDF), the FAQ (Markdown), the product catalogue (CSV and a PDF
catalogue), and a page per product (HTML). About 300 products, so a few hundred pages in all, and
every fact the ten questions ask about written down once, among near neighbours: there are eight
Ember headlamps and a dozen Kestrel jackets, and only one Ember 400 and one Kestrel 2.5L rating.

Fernhill Outfitters is invented, and so is everything it says. The generator is seeded, so every
run writes the same documents.
"""

from __future__ import annotations

import csv
import random
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from docx import Document
from fpdf import FPDF

SHOP = "Fernhill Outfitters"

REFUND_POLICY = [
    (
        "Our 30-day return window",
        "You can return most items within 30 days of delivery for a refund or an exchange. Clothing and footwear "
        "must be unworn, unwashed and still have the original tags attached; we cannot accept items that show signs "
        "of use outdoors, such as mud on soles or pilling on fleece. Gear that has been used on a trip is handled "
        "under the Trail Promise warranty instead, not as a return.",
    ),
    (
        "How refunds are paid",
        "Once your parcel reaches our returns centre in Harrogate we inspect it within two business days. Approved "
        "refunds go back to the original payment method within 5 business days of inspection, and we email you when "
        "it is done. Your bank may take a further three to five days to show the credit on your statement.",
    ),
    (
        "Store credit instead of a refund",
        "If you choose store credit rather than money back, we add a 10% bonus to the credit. Store credit is "
        "issued as a digital gift card and never expires.",
    ),
    (
        "Holiday returns",
        "Orders delivered between November 15 and December 24 can be returned until January 31 of the following "
        "year, so presents can be swapped after the holidays. The usual condition rules still apply.",
    ),
    (
        "Return shipping",
        "Returns are free when an item is faulty or we sent the wrong thing. For any other return within the United "
        "States we email you a prepaid return label and deduct $6.95 from your refund for it. You are welcome to use "
        "your own carrier instead, at your own cost; please keep the tracking number.",
    ),
    (
        "Items we cannot take back",
        "Items marked Final Sale, gift cards, and personalised items such as embroidered caps cannot be returned. "
        "For hygiene reasons, opened water filters and mouthpieces can only be returned if they are faulty.",
    ),
    (
        "Damaged or faulty on arrival",
        "If something arrives damaged, tell us within 7 days of delivery and send a photo of the damage and of the "
        "packaging. We will send a replacement straight away, or a full refund if the item is out of stock, and you "
        "do not need to return the damaged item first.",
    ),
    (
        "Exchanges",
        "We exchange a size or a colour once per order at no charge. Start an exchange from your order page, and we "
        "ship the new item as soon as the carrier scans your return.",
    ),
]

SHIPPING = [
    (
        "When your order ships",
        "Orders placed before 1:00 pm Eastern time on a business day ship the same day from our warehouse. Orders "
        "placed after that, or at the weekend or on a public holiday, ship on the next business day.",
    ),
    (
        "Delivery options and prices",
        "Standard delivery takes three to five business days and costs $5.95, or nothing on orders over $75. Express "
        "delivery takes two business days and costs $14.95. Overnight delivery is $29 and is available for orders "
        "placed before noon Eastern time.",
    ),
    (
        "International orders",
        "We ship to Canada, the United Kingdom and every country in the European Union. For the UK and the EU, duties "
        "and taxes are included in the price you pay at checkout, so nothing more is collected on delivery. Orders to "
        "Canada are shipped without duties paid, and the carrier may collect them when it delivers.",
    ),
    (
        "PO boxes and military addresses",
        "We can deliver to PO boxes and APO/FPO addresses with standard delivery only. Express and overnight "
        "delivery need a street address.",
    ),
    (
        "Lost or late parcels",
        "If tracking shows no movement for 10 business days, contact us and we will reship your order or refund it, "
        "whichever you prefer. We will handle the claim with the carrier.",
    ),
]

WARRANTY = [
    (
        "The Trail Promise",
        "Everything Fernhill makes is guaranteed against defects in materials and workmanship for two years from "
        "the date you bought it. If something fails because of how we made it, we will repair it, replace it or "
        "refund it, in that order of preference. Repairing first keeps gear in use and out of landfill.",
    ),
    (
        "Packs and bags",
        "Our packs and duffels carry a lifetime guarantee on seams and stitching: if a seam we sewed fails, we fix "
        "it, however old the pack. Zippers, buckles and other hardware are covered for two years, like the rest of "
        "the range. After that we repair hardware at cost, usually between $15 and $40.",
    ),
    (
        "What the Trail Promise does not cover",
        "Normal wear and tear, fading from sunlight, damage from accidents or misuse, and alterations made by "
        "someone other than us are not covered. Products from other brands that we sell carry their maker's "
        "warranty, and we will help you contact the maker.",
    ),
    (
        "Waterproof jackets",
        "Waterproof membranes are covered for two years against delamination and leaks at the seams. The water-"
        "repellent finish on the outer fabric wears off with use and is not covered; reproofing it every few months "
        "keeps the jacket breathing well.",
    ),
    (
        "How to make a claim",
        "Fill in the warranty form on our website with your order number and photos of the problem, then post the "
        "item to our repair centre with the claim number written on the parcel. Most repairs take three to four "
        "weeks, and we pay the return postage to you.",
    ),
    (
        "Repairs outside the warranty",
        "We repair gear outside the Trail Promise at cost. Send us photos first and we will quote before any work "
        "starts. Common repairs include replacing a jacket zipper, patching a tent fly and re-taping seams.",
    ),
]

FAQ = [
    (
        "Do gift cards expire?",
        "No. Fernhill gift cards never expire and carry no fees. They cannot be exchanged for cash, and a lost card "
        "can be replaced if you still have the purchase email.",
    ),
    (
        "Do you match prices?",
        "If we lower the price of an item within 14 days of your purchase, we refund the difference. We do not match "
        "other retailers' prices.",
    ),
    (
        "How do Trailhead Rewards work?",
        "Members earn one point for every dollar spent. 200 points can be swapped for $10 off a future order, and "
        "points expire after 18 months without a purchase.",
    ),
    (
        "Can I change or cancel my order?",
        "You can change or cancel an order within one hour of placing it from your order page. After that it may "
        "already be with our warehouse team, and you would need to return it instead.",
    ),
    (
        "How do I find my size?",
        "Each product page has a size chart. Our jackets are cut to fit over a fleece layer, so if you are between "
        "sizes, choose the smaller size for a trim fit or the larger one for room to layer.",
    ),
    (
        "When can I reach customer service?",
        "Our team answers email and chat Monday to Friday from 8 am to 6 pm Eastern time, and Saturdays from 10 am "
        "to 2 pm. We reply to every email within one business day.",
    ),
    (
        "Do you sell refurbished gear?",
        "Yes. Repaired and refurbished items appear in the Second Season section at up to 40% off, with the full "
        "Trail Promise.",
    ),
]

SERIES = {
    "Rain jackets": ["Kestrel", "Cirrus", "Tarn", "Fell", "Squall"],
    "Headlamps": ["Ember", "Glow", "Beacon"],
    "Backpacks": ["Ridgeline", "Talus", "Corrie", "Scree"],
    "Sleeping bags": ["Halcyon", "Nightjar", "Bivy"],
    "Tents": ["Basalt", "Juniper", "Larch"],
    "Hiking boots": ["Gritstone", "Bracken", "Moraine"],
    "Base layers": ["Merino Core", "Thermal Grid"],
    "Insulated jackets": ["Updraft", "Cinder", "Drift"],
    "Trekking poles": ["Cairn", "Spur"],
    "Water filters": ["Rill", "Spring"],
    "Fleece jackets": ["Bothy", "Hearth", "Shieling"],
    "Gloves": ["Gauntlet", "Grip", "Liner"],
    "Stoves": ["Flint", "Kindling"],
    "Trail shoes": ["Scramble", "Skyline", "Brook"],
    "Hats": ["Tor", "Crag"],
    "Socks": ["Hiker", "Trail Run"],
    # The same names as the rain jackets, on purpose: "Kestrel 2.5L" alone does not find the jacket.
    "Waterproof trousers": ["Kestrel", "Cirrus", "Tarn"],
}
NOUNS = {
    "Rain jackets": "rain jacket",
    "Headlamps": "headlamp",
    "Backpacks": "backpack",
    "Sleeping bags": "sleeping bag",
    "Tents": "tent",
    "Hiking boots": "hiking boots",
    "Base layers": "base layer",
    "Insulated jackets": "insulated jacket",
    "Trekking poles": "trekking poles",
    "Water filters": "water filter",
    "Fleece jackets": "fleece",
    "Gloves": "gloves",
    "Stoves": "stove",
    "Trail shoes": "trail shoes",
    "Hats": "hat",
    "Socks": "socks",
    "Waterproof trousers": "waterproof trousers",
}
VARIANTS = {
    "Rain jackets": ["2.5L", "3L", "3L Pro", "Lite", "Trail", "Alpine"],
    "Headlamps": ["200", "250", "300", "350", "400", "450", "500", "600"],
    "Backpacks": ["18", "24", "30", "38", "45", "55", "65"],
    "Sleeping bags": ["-7", "-2", "0", "4", "10"],
    "Tents": ["1P", "2P", "3P", "2P UL"],
    "Hiking boots": ["Mid GTX", "Low", "Mid", "Hi"],
    "Base layers": ["150 Crew", "200 Crew", "250 Zip", "150 Leggings"],
    "Insulated jackets": ["Hoody", "Jacket", "Vest"],
    "Trekking poles": ["Carbon", "Alloy", "Z-Fold"],
    "Water filters": ["Squeeze", "Gravity 4L", "Bottle"],
    "Fleece jackets": ["100 Half-zip", "200 Full-zip", "300 Hoody"],
    "Gloves": ["Light", "Mid", "Expedition"],
    "Stoves": ["Mini", "Duo", "Group"],
    "Trail shoes": ["GTX", "Standard", "Wide"],
    "Hats": ["Beanie", "Cap", "Sun hat"],
    "Socks": ["Light", "Mid", "Heavy"],
    "Waterproof trousers": ["2.5L", "3L", "Lite"],
}
FITS = ["Men's", "Women's"]
FITTED = {
    "Rain jackets",
    "Hiking boots",
    "Base layers",
    "Insulated jackets",
    "Fleece jackets",
    "Trail shoes",
    "Waterproof trousers",
}

#: The two facts the product questions ask for. Nothing else in the range may share these values.
EMBER_400_HIGH_HOURS = 6.5
KESTREL_25L_RATING = 28_000

INTROS = [
    "The {name} is built for {use}, and it is the one our staff reach for first.",
    "Designed in our studio and tested on {use}, the {name} keeps things simple where it matters.",
    "If your weekends mean {use}, the {name} is made for you.",
    "The {name} brings {feature} to {use} without adding weight you will notice.",
]
USES = [
    "wet days on the fells",
    "long weekends in the backcountry",
    "early starts and late finishes",
    "commutes that turn into adventures",
    "fast-and-light day hikes",
    "multi-day treks",
]
CARE = [
    "Machine wash cold on a gentle cycle and line dry. Do not use fabric softener.",
    "Wipe clean with a damp cloth and let it dry at room temperature, away from direct heat.",
    "Hand wash in lukewarm water with a technical wash, and dry flat in the shade.",
    "Rinse after use in salt water and store loosely, not compressed.",
]
TRIPS = [
    "a week of rain in the Cairngorms",
    "a winter traverse of the Brecon Beacons",
    "a summer on the Pennine Way",
    "three nights in the Lake District",
    "a wet autumn in Snowdonia",
    "a coastal walk in Cornwall",
    "the Tatras in early spring",
    "a fortnight on the Kungsleden",
]
FIELD = [
    "We took the {name} on {trip}, and it held up better than we expected.",
    "Our testers used the {name} through {trip}: nothing failed and nothing rubbed.",
    "On {trip}, the {name} was the piece of kit nobody complained about, which is the best review we know.",
    "After {trip}, the {name} came home a little muddier and otherwise exactly as it left.",
]
MATERIALS = [
    "recycled fabrics certified to the Global Recycled Standard",
    "bluesign-approved textiles",
    "responsibly sourced materials we can trace to the mill",
    "PFC-free water-repellent finishes",
    "recycled aluminium hardware",
]
MADE_IN = ["Portugal", "Vietnam", "Romania", "Taiwan", "Sri Lanka", "Poland"]
REVIEW_LINES = [
    "Exactly as described and well made.",
    "Fits true to size, and the colour is lovely in daylight.",
    "Used it every weekend since spring and it still looks new.",
    "A little heavier than I hoped, but very solid.",
    "Delivery was quick and the packaging was all recyclable.",
    "Bought a second one for my partner after the first trip.",
    "Kept me comfortable on a long, wet day in the hills.",
    "The details are thoughtful, especially the pockets and pulls.",
    "Great value compared with the big brands.",
    "Took a week to break in, then it was perfect.",
    "Customer service sorted out a sizing question within the hour.",
    "Light enough that I forget it is in the pack.",
    "Good, but I wish it came in more colours.",
    "Survived a slip on scree without a mark.",
    "Packs down small in a daypack.",
    "My third Fernhill purchase and the best so far.",
]
REVIEWERS = ["Priya", "Tom", "Aisha", "Marek", "Sofia", "Ewan", "Leila", "Jonas", "Hannah", "Kofi", "Maja", "Daniel"]
PLACES = ["Leeds", "Glasgow", "Bristol", "Kraków", "Dublin", "Denver", "Portland", "Bergen", "Lyon", "Toronto"]


@dataclass
class Product:
    sku: str
    name: str
    category: str
    price: int
    weight_g: int
    colours: list[str]
    specs: dict[str, str]
    features: list[str] = field(default_factory=list)


def headlamp_specs(rng: random.Random, variant: str, is_target: bool) -> dict[str, str]:
    lumens = int(variant)
    choices = [h for h in (3.0, 4.0, 4.5, 5.0, 5.5, 7.0, 7.5, 8.0, 9.0, 10.0) if h != EMBER_400_HIGH_HOURS]
    high = EMBER_400_HIGH_HOURS if is_target else rng.choice(choices)
    return {
        "Brightness": f"{lumens} lumens on high",
        "Battery life": f"{high:g} hours on high, {rng.randint(60, 200)} hours on low",
        "Battery": "rechargeable 2,000 mAh lithium-ion, USB-C",
        "Water resistance": rng.choice(["IPX4", "IPX6", "IPX7"]),
    }


def jacket_specs(rng: random.Random, is_target: bool) -> dict[str, str]:
    ratings = [r for r in (10_000, 15_000, 20_000, 25_000, 30_000) if r != KESTREL_25L_RATING]
    rating = KESTREL_25L_RATING if is_target else rng.choice(ratings)
    return {
        "Waterproof rating": f"{rating:,} mm",
        "Breathability": f"{rng.choice([15_000, 20_000, 25_000, 30_000]):,} g/m²/24h",
        "Fabric": rng.choice(["recycled nylon ripstop", "polyester plain weave", "nylon with a PFC-free finish"]),
        "Hood": rng.choice(["helmet-compatible, three-way adjustable", "fixed, with a laminated brim"]),
    }


def other_specs(rng: random.Random, category: str, variant: str) -> dict[str, str]:
    if category == "Backpacks":
        return {
            "Capacity": f"{variant} litres",
            "Back length": rng.choice(["S/M", "M/L", "one size"]),
            "Frame": "aluminium stay",
        }
    if category == "Sleeping bags":
        return {"Comfort rating": f"{variant} °C", "Fill": rng.choice(["650 fill-power duck down", "synthetic"])}
    if category == "Tents":
        return {"Sleeps": variant[0], "Packed size": f"{rng.randint(38, 55)} x {rng.randint(14, 20)} cm"}
    if category == "Hiking boots":
        return {"Upper": rng.choice(["nubuck leather", "synthetic mesh"]), "Sole": "Vibram Megagrip", "Drop": "10 mm"}
    if category == "Water filters":
        return {"Filter life": f"{rng.choice([1_000, 1_500, 4_000])} litres", "Removes": "99.9999% of bacteria"}
    return {"Material": rng.choice(["merino wool", "recycled polyester", "nylon and elastane"])}


def products(seed: int = 2026) -> list[Product]:
    rng = random.Random(seed)  # noqa: S311 - seeded so every run writes the same invented catalogue
    made: list[Product] = []
    colours = ["Moss", "Slate", "Ember red", "Storm blue", "Sand", "Black", "Heather"]
    for category, series_names in SERIES.items():
        for series in series_names:
            for variant in VARIANTS[category]:
                for fit in FITS if category in FITTED else [""]:
                    name = f"{series} {variant} {NOUNS[category]}" + (f", {fit}" if fit else "")
                    is_target_lamp = series == "Ember" and variant == "400"
                    is_target_jacket = category == "Rain jackets" and series == "Kestrel" and variant == "2.5L"
                    if category == "Headlamps":
                        specs = headlamp_specs(rng, variant, is_target_lamp)
                    elif category in ("Rain jackets", "Waterproof trousers"):
                        specs = jacket_specs(rng, is_target_jacket)
                    else:
                        specs = other_specs(rng, category, variant)
                    made.append(
                        Product(
                            sku=f"FH-{len(made) + 1:04d}",
                            name=name,
                            category=category,
                            price=rng.randrange(25, 450, 5),
                            weight_g=rng.randrange(80, 2400, 10),
                            colours=rng.sample(colours, 3),
                            specs=specs,
                            features=rng.sample(
                                [
                                    "taped seams throughout",
                                    "made with recycled materials",
                                    "packs into its own pocket",
                                    "reflective details for low light",
                                    "covered by the Trail Promise",
                                    "fits under a pack hip belt",
                                    "tested in our Lake District field lab",
                                    "repairable with standard parts",
                                ],
                                4,
                            ),
                        )
                    )
    return made


def product_page(product: Product, rng: random.Random) -> str:
    name = product.name
    intro = rng.choice(INTROS).format(name=name, use=rng.choice(USES), feature=product.features[0])
    rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in product.specs.items())
    features = "".join(f"<li>{escape(f)}</li>" for f in product.features)
    care = rng.choice(CARE)
    material = rng.choice(MATERIALS)
    country = rng.choice(MADE_IN)
    field_notes = " ".join(rng.choice(FIELD).format(name=name, trip=trip) for trip in rng.sample(TRIPS, 2))
    reviews = "".join(
        f"<p>{escape(' '.join(rng.sample(REVIEW_LINES, 2)))} - {rng.choice(REVIEWERS)}, {rng.choice(PLACES)}, "
        f"{rng.randint(3, 5)} out of 5</p>"
        for _ in range(4)
    )
    pool = [
        (f"Can I put the {name} in the washing machine?", care),
        (
            f"Is the {name} covered by a warranty?",
            "Yes. Like everything we make, it is covered by the two-year Trail Promise.",
        ),
        (
            f"Can the {name} be repaired?",
            "Yes. Our repair centre stocks parts for it, and repairs outside the warranty are at cost.",
        ),
        (f"Does the {name} come in other colours?", f"This season it comes in {', '.join(product.colours)}."),
        (f"Where is the {name} made?", f"In {country}, in a factory we audit every year."),
    ]
    questions = "".join(f"<p><strong>{escape(q)}</strong> {escape(a)}</p>" for q, a in rng.sample(pool, 3))
    return (
        f"<!doctype html><html><head><title>{escape(name)} | {SHOP}</title></head><body>"
        f"<h1>{escape(name)}</h1><p>{escape(intro)} Price: ${product.price}. "
        f"Colours: {', '.join(product.colours)}. Weight: {product.weight_g} g.</p>"
        f"<h2>Features</h2><ul>{features}</ul>"
        f"<h2>Specifications</h2><table><tr><th>Specification</th><th>Value</th></tr>{rows}</table>"
        f"<h2>In the field</h2><p>{escape(field_notes)}</p>"
        f"<h2>Materials and making</h2><p>We make the {escape(name)} in {country} with {escape(material)}. "
        f"Every part is chosen so it can be repaired rather than replaced, and the trims can be swapped when they wear.</p>"
        f"<h2>Care</h2><p>{escape(care)}</p>"
        f"<h2>What customers say</h2>{reviews}"
        f"<h2>Questions</h2>{questions}"
        f"</body></html>"
    )


def write_docx(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    document = Document()
    document.add_heading(title, level=1)
    for heading, text in sections:
        document.add_heading(heading, level=2)
        document.add_paragraph(text)
    document.save(str(path))


def write_html(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    body = "".join(f"<h2>{escape(h)}</h2><p>{escape(t)}</p>" for h, t in sections)
    path.write_text(
        f"<!doctype html><html><head><title>{escape(title)}</title></head><body><h1>{escape(title)}</h1>{body}</body></html>"
    )


def write_markdown(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    path.write_text(f"# {title}\n\n" + "".join(f"## {h}\n\n{t}\n\n" for h, t in sections), encoding="utf-8")


def _pdf() -> FPDF:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    return pdf


def _lines(pdf: FPDF, height: float, text: str) -> None:
    """A paragraph, leaving the cursor at the left margin of the next line."""
    pdf.multi_cell(0, height, text, new_x="LMARGIN", new_y="NEXT")


def write_warranty_pdf(path: Path, sections: list[tuple[str, str]]) -> None:
    pdf = _pdf()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    _lines(pdf, 9, "The Fernhill Trail Promise")
    for heading, text in sections:
        pdf.add_page()  # one section a page, so citations can name a page
        pdf.set_font("Helvetica", "B", 13)
        _lines(pdf, 8, heading)
        pdf.set_font("Helvetica", size=11)
        _lines(pdf, 6, text.replace("–", "-"))
    pdf.output(str(path))


def write_catalogue_pdf(path: Path, items: list[Product]) -> None:
    pdf = _pdf()
    for start in range(0, len(items), 3):
        pdf.add_page()
        for product in items[start : start + 3]:
            pdf.set_font("Helvetica", "B", 12)
            _lines(pdf, 7, f"{product.name} ({product.sku}) - ${product.price}")
            pdf.set_font("Helvetica", size=10)
            specs = "; ".join(f"{k}: {v}" for k, v in product.specs.items()).replace("°", " deg").replace("²", "2")
            _lines(
                pdf,
                5,
                f"{product.category}. {specs}. Weight {product.weight_g} g. Colours: {', '.join(product.colours)}.",
            )
            pdf.ln(3)
    pdf.output(str(path))


def write_csv(path: Path, items: list[Product]) -> None:
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["sku", "name", "category", "price_usd", "weight_g", "colours", "specs"])
        for p in items:
            writer.writerow(
                [
                    p.sku,
                    p.name,
                    p.category,
                    p.price,
                    p.weight_g,
                    "|".join(p.colours),
                    "; ".join(f"{k}: {v}" for k, v in p.specs.items()),
                ]
            )


def build(directory: Path) -> dict[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "products").mkdir(exist_ok=True)
    rng = random.Random(7)  # noqa: S311 - seeded, for the same reason
    items = products()
    write_docx(directory / "refund_policy.docx", "Returns and refunds", REFUND_POLICY)
    write_html(directory / "shipping.html", "Shipping and delivery", SHIPPING)
    write_warranty_pdf(directory / "warranty.pdf", WARRANTY)
    write_markdown(directory / "faq.md", "Frequently asked questions", FAQ)
    write_csv(directory / "product_catalog.csv", items)
    write_catalogue_pdf(directory / "catalogue_2026.pdf", items)
    for product in items:
        (directory / "products" / f"{product.sku}.html").write_text(product_page(product, rng), encoding="utf-8")
    return {"products": len(items), "files": sum(1 for f in directory.rglob("*") if f.is_file())}


if __name__ == "__main__":
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "corpus")))
