"""Why an untapped holder has data it doesn't sell: ten archetypes, defined once as data.

The hypotheses step uses them as a checklist so searches cover every archetype, not just the
obvious service by-product. The screen labels each untapped holder with one, the table shows it,
and the email picks its pitch from it. SUPPLIER_TOOLS feeds the discovery channel that finds
holders through the customer lists of the tools they run on.
"""

from dataclasses import dataclass
from typing import Literal

from worker.brief_template import AUDIO, GEO, IMAGE, SENSOR, TABULAR, TEXT, VIDEO


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    why: str
    examples: dict[str, tuple[str, ...]]  # modality group -> examples; "any" applies to all
    pitch: str  # the angle an outreach email takes
    reuse_risk: bool = False  # recorded for a purpose that may not allow reuse for AI training


# Modality (A1 choices) -> the example group it reads
GROUP = {AUDIO: "audio", VIDEO: "visual", IMAGE: "visual", TEXT: "text", TABULAR: "records", SENSOR: "records",
         GEO: "records"}

ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        "service", "Service by-product", "its product or service records the data as it runs",
        {"audio": ("meeting and call-recording tools", "contact centres and outsourcers", "telehealth platforms",
                   "ride-hailing safety audio", "drive-thru voice ordering"),
         "visual": ("video-call platforms", "telehealth video", "retail analytics cameras"),
         "text": ("customer-support chat platforms", "helpdesk software", "community forums"),
         "records": ("booking and dispatch systems", "loyalty programmes", "payment terminals")},
        "The recordings your service already captures could be licensed for AI training with the right consent.",
        reuse_risk=True),
    Archetype(
        "kept_by_law", "Kept by law", "regulation makes it record and keep the data for years",
        {"audio": ("bank and broker trader calls (MiFID II)", "insurance claims lines", "debt collectors",
                   "emergency dispatch", "alarm-monitoring centres"),
         "visual": ("CCTV kept by casinos, banks and transport operators", "body-worn camera footage"),
         "text": ("regulated communications archives", "complaints records"),
         "records": ("transaction records", "safety and maintenance logs", "waste transfer notes")},
        "You already pay to store recordings you must keep; part of that cost could be recovered.",
        reuse_risk=True),
    Archetype(
        "production_leftovers", "Production leftovers", "content work leaves far more raw material than it publishes",
        {"audio": ("unused takes at dubbing and voice-over studios", "game voice sessions", "raw podcast multitracks",
                   "e-learning narration"),
         "visual": ("unpublished stock footage", "wedding and event videographers' raw footage", "TV rushes"),
         "text": ("translation memories", "unpublished drafts and editorial archives"),
         "records": ("survey fieldwork raw files",)},
        "Unused takes and raw material sitting on your drives could earn revenue."),
    Archetype(
        "labelled_by_workflow", "Labelled by its own workflow", "it already pays people to label data for its business",
        {"audio": ("subtitling and captioning firms (audio aligned to text)", "court reporters and deposition firms",
                   "interpreting agencies", "medical dictation services"),
         "visual": ("insurance claim photo assessors", "quality-inspection teams"),
         "text": ("content moderation teams", "translation agencies"),
         "records": ("claims adjusters' coded records",)},
        "Your transcripts and labels are exactly what AI developers pay for."),
    Archetype(
        "dormant", "Dormant archive", "the business closed, pivoted or was acquired, and the data sits in storage",
        {"any": ("shut-down startups", "apps sunset after an acquisition", "companies in administration",
                 "closed radio and TV stations", "old conference-bridge providers")},
        "Data from a closed or retired product is a cost in storage; it could become revenue."),
    Archetype(
        "device_fleet", "Device fleet", "its hardware records the world",
        {"audio": ("in-car voice assistants", "smart speakers in hotels", "hearing devices"),
         "visual": ("fleet dashcams and telematics", "smart doorbells", "farm and inspection drones",
                    "warehouse CCTV", "3D property scans"),
         "records": ("bin and weighbridge sensors", "lift and HVAC sensors", "smart meters", "weather stations")},
        "The footage and readings your devices already collect have value beyond your own product."),
    Archetype(
        "assessment", "Assessment and teaching", "it records people to test or teach them",
        {"audio": ("spoken-English test providers", "language schools", "speech therapy clinics",
                   "lecture capture at universities"),
         "visual": ("driving schools' dashcams", "sports coaching video", "lecture capture"),
         "text": ("exam boards' marked scripts", "essay-feedback platforms")},
        "Recordings from tests and lessons are rare, well-described data."),
    Archetype(
        "monitoring", "Monitoring and listening", "its job is to record other people's content",
        {"audio": ("media-monitoring firms recording TV and radio", "in-game voice-chat moderation"),
         "visual": ("broadcast monitoring", "ad verification"),
         "text": ("social-listening and press-clipping services",)},
        "The broadcast archive you build for clients could serve a second market."),
    Archetype(
        "research", "Research leftovers", "a funded project or study ended and the data stayed",
        {"any": ("finished EU or national research projects", "market-research fieldwork agencies",
                 "user-research platforms", "clinical trial sites")},
        "Data from finished projects can fund the next one."),
    Archetype(
        "community", "Community and events", "it records sessions for members or the public",
        {"audio": ("conference organisers' session recordings", "religious sermon archives",
                   "council and town-hall meetings"),
         "visual": ("sports clubs' match footage", "event livestream archives"),
         "text": ("member forums", "meeting minutes archives")},
        "Your session archive could be licensed while keeping your members' trust."),
)

BY_KEY = {a.key: a for a in ARCHETYPES}
ArchetypeKey = Literal.__getitem__(tuple(BY_KEY))

# Tools untapped holders run on: their case studies and logo walls name the holders
SUPPLIER_TOOLS = {
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
        lines.append(f'- "{a.key}" ({a.label}): {a.why}' + (f"; e.g. {', '.join(examples)}" if examples else ""))
    return "\n".join(lines)


def supplier_tools(modalities: list[str]) -> list[str]:
    return [t for g in groups(modalities) for t in SUPPLIER_TOOLS[g]]
