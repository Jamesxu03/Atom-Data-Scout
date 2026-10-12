"""Why an untapped holder has data it doesn't sell: ten archetypes, defined once as data.

The hypotheses step, the "signals" discovery channel and the screen use them as a checklist, so
searches think of banks, subtitling firms and device makers, not just the obvious service
by-product. The screen also names the archetype of each untapped holder: dormant archives and
leftovers start with a better licence prior (scoring.py), and later discovery rounds look for
archetypes nothing was found for yet. Examples lead with the organisations that hold the data,
not the tools that record it. SUPPLIER_TOOLS names the tools whose customer stories lead to
holders.
"""

from dataclasses import dataclass

from worker.brief_template import AUDIO, GEO, IMAGE, SENSOR, TABULAR, TEXT, VIDEO


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    why: str
    examples: dict[str, tuple[str, ...]]  # modality group -> examples; "any" applies to all


# Modality (A1 choices) -> the example group it reads
GROUP = {AUDIO: "audio", VIDEO: "visual", IMAGE: "visual", TEXT: "text", TABULAR: "records", SENSOR: "records",
         GEO: "records"}

ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        "service", "Service by-product", "its product or service records the data as it runs",
        {"audio": ("enterprises and professional firms that record client and internal meetings",
                   "contact-centre outsourcers serving banks and insurers",
                   "training providers with recorded workshops", "telehealth platforms",
                   "ride-hailing safety audio", "drive-thru voice ordering", "meeting and call-recording tools"),
         "visual": ("video-call platforms", "telehealth video", "retail analytics cameras"),
         "text": ("customer-support chat platforms", "helpdesk software", "community forums"),
         "records": ("booking and dispatch systems", "loyalty programmes", "payment terminals")}),
    Archetype(
        "kept_by_law", "Kept by law",
        "regulation makes it record and keep the data for years; usually a big or regulated institution, "
        "in scope only when it is distressed, has shared data before, or a law now allows reuse",
        {"audio": ("bank and broker trader calls (MiFID II)", "insurance claims lines", "debt collectors",
                   "emergency dispatch", "alarm-monitoring centres"),
         "visual": ("CCTV kept by casinos, banks and transport operators", "body-worn camera footage"),
         "text": ("regulated communications archives", "complaints records"),
         "records": ("transaction records", "safety and maintenance logs", "waste transfer notes")}),
    Archetype(
        "production_leftovers", "Production leftovers", "content work leaves far more raw material than it publishes",
        {"audio": ("unused takes at dubbing and voice-over studios", "game voice sessions", "raw podcast multitracks",
                   "e-learning narration"),
         "visual": ("unpublished stock footage", "wedding and event videographers' raw footage", "TV rushes"),
         "text": ("translation memories", "unpublished drafts and editorial archives"),
         "records": ("survey fieldwork raw files",)}),
    Archetype(
        "labelled_by_workflow", "Labelled by its own workflow", "it already pays people to label data for its business",
        {"audio": ("subtitling and captioning firms (audio aligned to text)", "court reporters and deposition firms",
                   "interpreting agencies", "medical dictation services"),
         "visual": ("insurance claim photo assessors", "quality-inspection teams"),
         "text": ("content moderation teams", "translation agencies"),
         "records": ("claims adjusters' coded records",)}),
    Archetype(
        "dormant", "Dormant archive", "the business closed, pivoted or was acquired, and the data sits in storage",
        {"any": ("shut-down startups", "apps sunset after an acquisition", "companies in administration",
                 "closed radio and TV stations", "old conference-bridge providers")}),
    Archetype(
        "device_fleet", "Device fleet", "its hardware records the world",
        {"audio": ("in-car voice assistants", "smart speakers in hotels", "hearing devices"),
         "visual": ("fleet dashcams and telematics", "smart doorbells", "farm and inspection drones",
                    "warehouse CCTV", "3D property scans"),
         "records": ("bin and weighbridge sensors", "lift and HVAC sensors", "smart meters", "weather stations")}),
    Archetype(
        "assessment", "Assessment and teaching", "it records people to test or teach them",
        {"audio": ("spoken-English test providers", "language schools", "speech therapy clinics",
                   "lecture capture at universities"),
         "visual": ("driving schools' dashcams", "sports coaching video", "lecture capture"),
         "text": ("exam boards' marked scripts", "essay-feedback platforms")}),
    Archetype(
        "monitoring", "Monitoring and listening", "its job is to record other people's content",
        {"audio": ("media-monitoring firms recording TV and radio", "in-game voice-chat moderation"),
         "visual": ("broadcast monitoring", "ad verification"),
         "text": ("social-listening and press-clipping services",)}),
    Archetype(
        "research", "Research leftovers", "a funded project or study ended and the data stayed",
        {"any": ("finished EU or national research projects", "market-research fieldwork agencies",
                 "user-research platforms", "clinical trial sites")}),
    Archetype(
        "community", "Community and events", "it records sessions for members or the public",
        {"audio": ("conference organisers' session recordings", "religious sermon archives",
                   "council and town-hall meetings"),
         "visual": ("sports clubs' match footage", "event livestream archives"),
         "text": ("member forums", "meeting minutes archives")}),
)

BY_KEY = {a.key: a for a in ARCHETYPES}

# Tools untapped holders run on, by name: each one's customer stories, case studies and logo
# walls name organisations that record this data with it (one discovery call per tool)
SUPPLIER_TOOLS = {
    "audio": ("Gong", "NICE", "Verint", "Genesys", "Five9", "Calabrio", "Otter", "Fireflies", "Zoom", "Panopto",
              "Kaltura"),
    "visual": ("Samsara", "Lytx", "Motive", "Milestone", "Genetec", "Verkada", "DroneDeploy", "Matterport"),
    "text": ("Zendesk", "Intercom", "Freshdesk", "LivePerson", "Smartling", "Phrase"),
    "records": ("Samsara", "Geotab", "AMCS", "Itron", "Siemens MindSphere"),
}
# The same tools as families, for prompts that search an industry rather than one tool
SUPPLIER_FAMILIES = {
    "audio": ("speech-to-text APIs", "call-recording and contact-centre platforms", "conversation intelligence",
              "lecture-capture systems", "dubbing and localisation software"),
    "visual": ("dashcam and telematics providers", "video management and CCTV software", "drone software",
               "3D scanning platforms"),
    "text": ("helpdesk and live-chat software", "translation management systems", "moderation services"),
    "records": ("IoT and sensor platforms", "fleet and waste-management software", "weighbridge systems"),
}


def groups(modalities: list[str]) -> list[str]:
    """Example groups for the brief's modalities; all of them when none is picked."""
    picked = list(dict.fromkeys(GROUP[m] for m in modalities if m in GROUP))
    return picked or ["audio", "visual", "text", "records"]


def guide(modalities: list[str]) -> str:
    """The archetypes with examples for these modalities, for prompts."""
    lines = []
    for a in ARCHETYPES:
        examples = [e for g in ["any", *groups(modalities)] for e in a.examples.get(g, ())]
        lines.append(f"- {a.label}: {a.why}" + (f"; e.g. {', '.join(examples)}" if examples else ""))
    return "\n".join(lines)


def supplier_tools(modalities: list[str]) -> list[str]:
    """Named tools for these modalities, without repeats."""
    return list(dict.fromkeys(t for g in groups(modalities) for t in SUPPLIER_TOOLS[g]))


def supplier_families(modalities: list[str]) -> list[str]:
    return [t for g in groups(modalities) for t in SUPPLIER_FAMILIES[g]]


def keys_text() -> str:
    """One line per archetype for the screen: key and label."""
    return "\n".join(f"- {a.key}: {a.label}, {a.why}" for a in ARCHETYPES)


def with_examples(modalities: list[str]) -> list[Archetype]:
    """Archetypes that have examples for these modalities: the ones worth searching for."""
    return [a for a in ARCHETYPES if any(a.examples.get(g) for g in ["any", *groups(modalities)])]
