"""Fake results so the UI can be built and tested without any API keys. All companies are fictional."""

from worker.schemas import CompanyProfile, Evidence, Result, Score


def _ev(quote: str, url: str) -> list[Evidence]:
    return [Evidence(quote=quote, url=url)]


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
            company_number=number,
            company_status=status,
            incorporated_on="2017-03-01",
            registered_address="1 Example Street, London",
        )
        dims = ["data_match", "estimated_volume", "rights_clarity", "seller_motivation", "contactability"]
        score = Score(
            **dict(zip(dims, pts)),
            reasons={d: f"(demo) reason for {d.replace('_', ' ')}" for d in dims},
        )
        email = (
            f"Subject: Enquiry about licensing meeting audio data\n\n"
            f"Dear {name} team,\n\n"
            f"We are looking to licence multi-speaker English meeting audio with transcripts for "
            f"model training. Given your work in this area, we'd welcome a short call to discuss "
            f"whether a data licence could be of interest.\n\nBest regards,\n[Your name]"
        )
        results.append(Result(profile=profile, score=score, email_draft=email))
    return results
