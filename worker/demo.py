"""Fake results so the UI can be built and tested without any API keys. All companies are fictional."""

from worker.schemas import Candidate, CompanyProfile, Contact, Evidence, Result, Score


def _ev(quote: str, url: str) -> list[Evidence]:
    return [Evidence(quote=quote, url=url)]


# Fictional people; phone numbers are from Ofcom's 020 7946 0xxx range reserved for drama
DEMO_CONTACTS = {
    "examplecall.example": [
        ("Alex Example", "Co-founder & CEO", "alex@examplecall.example", None, "search", True),
        ("Jordan Sample", "Joint Administrator (Example Insolvency LLP)", "jordan.sample@exampleinsolvency.example", "+44 20 7946 0123", "search", True),
        (None, "Privacy / DPO inbox", "privacy@examplecall.example", None, "website", True),
    ],
    "sampletranscribe.example": [
        ("Sam Placeholder", "Head of Data Partnerships", None, None, "search", False),
        (None, "General inbox", "hello@sampletranscribe.example", "+44 20 7946 0456", "website", True),
    ],
    "demomeetings.example": [
        ("Riley Mock", "Chief Technology Officer", "riley@demomeetings.example", None, "search", True),
        (None, "Sales / partnerships inbox", "partnerships@demomeetings.example", None, "website", True),
    ],
}


def _demo_contacts(domain: str) -> list[Contact]:
    return [
        Contact(name=name, role=role, email=email, phone=phone, origin=origin, verified=verified,
                source_url=f"https://{domain}/{'contact' if origin == 'website' else 'about/team'}")
        for name, role, email, phone, origin, verified in DEMO_CONTACTS.get(domain, [])
    ]


def demo_results() -> list[Result]:
    rows = [
        (
            "Examplecall Ltd", "https://examplecall.example", True, "24 months", True, True,
            "12345678", "administration", (5, 4, 4, 5, 4),
        ),
        (
            "Demo Meetings Co", "https://demomeetings.example", True, "90 days", False, None,
            "23456789", "active", (4, 3, 2, 2, 4),
        ),
        (
            "Sample Transcribe plc", "https://sampletranscribe.example", True, "indefinite", True, False,
            "34567890", "liquidation", (4, 5, 3, 5, 2),
        ),
    ]
    results = []
    for name, site, rec, ret, transfer, training, number, status, pts in rows:
        privacy = f"{site}/privacy"
        profile = CompanyProfile(
            name=name,
            website=site,
            records_data=rec,
            records_data_evidence=_ev("We record audio of meetings you choose to capture.", privacy),
            retention=ret,
            retention_evidence=_ev(f"Recordings are retained for {ret}.", privacy),
            policy_allows_transfer=transfer,
            policy_allows_transfer_evidence=_ev(
                "In the event of a merger or sale of assets, data may be transferred.", privacy
            ),
            policy_allows_training=training,
            policy_allows_training_evidence=_ev(
                "We may use de-identified data to improve our models.", privacy
            ),
            country="GB",
            registry="Companies House",
            registration_number=number,
            company_status=status,
            incorporated_on="2017-03-01",
            registered_address="1 Example Street, London",
        )
        dims = ["data_match", "estimated_volume", "rights_clarity", "seller_motivation", "contactability"]
        score = Score(
            **dict(zip(dims, pts)),
            reasons={d: f"(demo) reason for {d.replace('_', ' ')}" for d in dims},
        )
        contacts = _demo_contacts(site.removeprefix("https://"))
        greeting = next((k.name.split()[0] for k in contacts if k.name), f"{name} team")
        email = (
            f"Subject: Enquiry about licensing meeting audio data\n\n"
            f"Dear {greeting},\n\n"
            f"We are looking to licence multi-speaker English meeting audio with transcripts for "
            f"model training. Given your work in this area, we'd welcome a short call to discuss "
            f"whether a data licence could be of interest.\n\nBest regards,\n[Your name]"
        )
        results.append(Result(profile=profile, score=score, email_draft=email, contacts=contacts))
    return results


def demo_candidates() -> list[Candidate]:
    rows = [
        ("Examplecall Ltd", "examplecall.example", ["product_search", "directory"], "yes", "AI notetaker that records and transcribes meetings"),
        ("Sample Transcribe plc", "sampletranscribe.example", ["product_search", "snowball"], "yes", "Transcription service for recorded business calls"),
        ("Demo Meetings Co", "demomeetings.example", ["snowball"], "yes", "Video conferencing with cloud recording"),
        ("Placeholder Voice", "placeholdervoice.example", ["marketplace"], "maybe", "Lists speech datasets; unclear if meeting audio"),
        ("Mock CRM Inc", "mockcrm.example", ["directory"], "maybe", "Sales CRM with an optional call-recording add-on"),
        ("Fictional HR", "fictionalhr.example", ["product_search"], "no", "HR payroll software; no audio"),
        ("Invented Ads", "inventedads.example", ["directory"], "no", "Ad network; unrelated"),
    ]
    return [
        Candidate(
            name=name, website=f"https://{domain}", domain=domain, channels=channels,
            source_urls=[f"https://search.example/{domain}"], relevance=label, relevance_reason=reason,
            contacts=_demo_contacts(domain), source_verified=label != "no",
        )
        for name, domain, channels, label, reason in rows
    ]
