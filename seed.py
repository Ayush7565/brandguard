"""Demo data: a fictional brand ("Lumora", reusable stainless-steel bottles) so judges can try the
product immediately and no real confidential material is needed."""
import json
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

DEMO_PASSWORD = "demo1234"

USERS = [
    ("Priya Sharma", "creator@lumora.demo", "creator"),
    ("Marcus Lee", "manager@lumora.demo", "manager"),
    ("Rhea Patel", "reviewer@lumora.demo", "reviewer"),
    ("Asha Verma", "admin@lumora.demo", "admin"),
]

GUIDELINES = [
    ("Brand voice and tone", "tone",
     "Lumora speaks in a warm, clear and confident voice. We sound like a knowledgeable friend, not a salesperson. "
     "Use at most one exclamation mark per piece of content. "
     "Avoid writing in all capital letters; use plain emphasis instead. "
     "Avoid slang such as 'gonna' or 'wanna' everywhere, and hype words such as 'epic', 'insane' or 'game-changer' "
     "in press releases and on the website. "
     "Keep emojis to a maximum of two per social post and none in press releases."),
    ("Claims and promises", "claims",
     "Every promotional claim must be supported by an approved source. "
     "Do not guarantee outcomes or use absolute words such as 'guaranteed', '100%', 'risk-free' or 'miracle'. "
     "Do not use unsupported superlatives such as 'best', '#1', 'unbeatable' or 'world-class'. "
     "Describe what the product is designed to do instead of promising results. "
     "Only the approved claims in the claims library may be used."),
    ("Accessibility", "accessibility",
     "All images must include meaningful alt text. "
     "Link text must describe the destination, so do not use 'click here' or 'read more' on their own. "
     "Aim for a reading level of grade 9 or below, and keep sentences under 25 words where possible."),
    ("Visual identity", "visual",
     "Use only the approved palette: Lumora Green #1F6F5B, Deep Navy #12263A, Sand #F2E8DC and Coral #E86A4A. "
     "Do not introduce other colours in branded content."),
    ("Messaging pillars", "messaging",
     "Our core messages are durable bottles made from recycled stainless steel, honest sustainability with transparent "
     "numbers, and everyday design. Never criticise competitors by name. "
     "Talk about what Lumora does, not about what other brands get wrong."),
]

# (key, statement, keywords, value)
FACTS = [
    ("founded", "Lumora was founded in 2018.", "founded, established, since", "2018"),
    ("countries", "Lumora ships to 32 countries.", "ships, shipping, delivers, countries", "32 countries"),
    ("recycled steel", "Every Lumora bottle contains 90% recycled stainless steel.", "recycled", "90%"),
    ("warranty", "Lumora bottles come with a 5-year warranty.", "warranty, warrantied", "5-year warranty"),
]

# (preferred, banned aliases, note)
TERMS = [
    ("bottle", "flask, canteen", "Our product category is 'bottle', not 'flask' or 'canteen'."),
    ("customers", "consumers, end users", "We say 'customers' to keep the tone personal."),
    ("made with recycled stainless steel", "eco-friendly, planet-friendly",
     "Be specific: say what the bottle is made of instead of the vague 'eco-friendly'."),
]

CLAIMS = [
    ("free shipping on orders over $50", "Approved: shipping policy, 2026."),
    ("5-year warranty", "Approved: warranty terms, 2026."),
]

ASSETS = [("Lumora Green", "#1F6F5B"), ("Deep Navy", "#12263A"), ("Sand", "#F2E8DC"), ("Coral", "#E86A4A")]

SAMPLES = {
    "social": (
        "Spring drop on Instagram", "social",
        "Announce the spring bottle collection and get people to visit the shop.",
        "\U0001F680 Meet the BEST flask on the planet!!! Lumora has been shipping to 25 countries since 2015 and our "
        "eco-friendly steel is 100% guaranteed to keep your drinks cold for 48 hours. Tap [click here](https://lumora.example/shop) "
        "to order. Our new coral is #FF5533 and it's gonna sell out fast!\n\n![](https://lumora.example/img/spring.png)"),
    "press": (
        "Press release: new factory", "press_release",
        "Announce the new recycling partnership to journalists.",
        "Lumora, founded in 2018, today announced a partnership with a regional steel recycler. Every Lumora bottle "
        "now contains 100% recycled stainless steel, and the company ships to 40 countries. Our customers receive a "
        "10-year warranty with every bottle. This epic move is a game-changer and the best news in the industry."),
    "clean": (
        "Spring newsletter intro", "email",
        "Thank customers and introduce the spring collection.",
        "Thank you for choosing Lumora. Our spring collection is here, with three new colours from our approved palette. "
        "Every bottle contains 90% recycled stainless steel and comes with a 5-year warranty. "
        "Enjoy free shipping on orders over $50. See the full range on our [spring collection page](https://lumora.example/spring)."),
}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def seed_users_and_kb(db):
    """Insert demo users and knowledge base. `db` is an sqlite3 connection."""
    pw = generate_password_hash(DEMO_PASSWORD)
    for name, email, role in USERS:
        db.execute("INSERT INTO users(name, email, password_hash, role) VALUES (?,?,?,?)", (name, email, pw, role))
    t = now()

    def kb(kind, title, body, extra):
        db.execute("INSERT INTO kb_items(kind, title, body, extra, updated_at) VALUES (?,?,?,?,?)",
                   (kind, title, body, json.dumps(extra), t))

    for title, topic, body in GUIDELINES:
        kb("guideline", title, body, {"topic": topic})
    for key, statement, keywords, value in FACTS:
        kb("fact", key, statement, {"keywords": keywords, "value": value})
    for preferred, banned, note in TERMS:
        kb("term", preferred, note, {"banned": banned})
    for phrase, note in CLAIMS:
        kb("claim", phrase, note, {})
    for name, hexv in ASSETS:
        kb("asset", name, hexv, {"type": "color"})
    db.commit()
