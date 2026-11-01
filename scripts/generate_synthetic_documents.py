from __future__ import annotations

import argparse
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "synthetic_data" / "documents"

BASE = {
    "club": "Harbor City FC",
    "country": "Kenya",
    "league": "Kenyan Premier League",
    "sender": "Grace Njeri",
    "email": "recruitment@harborcityfc.ke",
    "phone": "+254700000111",
    "agent": "Taylor Sample",
    "agent_phone": "+254700000333",
    "agent_email": "taylor@synthetic-agents.example",
    "agent_licence": "SYNTH-AGENT-001",
    "signatory": "Grace Njeri",
    "role": "Director of Football",
    "trial": "Trial date: 2027-07-12",
    "location": "Trial location: Harbor City training ground",
    "arrival": "Arrival date: 2027-07-10",
    "departure": "Departure date: 2027-07-18",
    "travel": "Travel arrangements: Player arranges travel independently",
    "accommodation": "Accommodation: Club guesthouse during trial",
    "registration": "Registration date: 2027-07-15",
    "contract_date": "Contract date: 2027-06-20",
    "start_date": "Proposed start date: 2027-07-15",
    "status": "Player status: under contract",
    "fees": "No fees are requested from the player.",
}


def make_letter(**changes: str) -> tuple[str, str | None]:
    values = {**BASE, **changes}
    body = "\n".join(
        [
            "SYNTHETIC DEMO DOCUMENT - NOT A REAL RECRUITMENT OFFER",
            "Source: synthetic_demo",
            f"Club: {values['club']}",
            f"Country: {values['country']}",
            f"League: {values['league']}",
            f"From: {values['sender']}",
            f"Email: {values['email']}",
            f"Phone: {values['phone']}",
            f"Agent: {values['agent']}",
            f"Agent phone: {values['agent_phone']}",
            f"Agent email: {values['agent_email']}",
            f"Agent licence number: {values['agent_licence']}",
            f"Signatory: {values['signatory']}",
            f"Role: {values['role']}",
            values["trial"],
            values["location"],
            values["arrival"],
            values["departure"],
            values["accommodation"],
            values["travel"],
            values["contract_date"],
            values["start_date"],
            values["registration"],
            values["status"],
            "Contract duration: 2 years",
            "Salary: USD 1,200 per month",
            "Benefits: accommodation during the trial",
            values["fees"],
            "Visa and immigration decisions remain with the relevant authorities.",
            "All people, clubs, contacts, and events in this document are fictional.",
        ]
    )
    creator = values.get("creator", "OffsAid synthetic document generator")
    return body, creator


SCENARIOS = {
    "01_clean.pdf": {},
    "02_gmail_sender.pdf": {"email": "harborcity.recruitment@gmail.com"},
    "03_lookalike_domain.pdf": {
        "club": "Example FC",
        "country": "England",
        "league": "Premier League",
        "sender": "Thomas Reid",
        "email": "recruitment@example-fc.com",
        "signatory": "Thomas Reid",
        "role": "Head of Recruitment",
    },
    "04_recent_domain.pdf": {"email": "recruitment@newclubofficial.net"},
    "05_personal_payment.pdf": {
        "fees": "Urgent payment request: Pay KSh 25,000 to my personal M-Pesa number +254700000999 by 2026-11-05 to secure your slot."
    },
    "06_unknown_signatory.pdf": {"signatory": "Jordan Sample", "role": "Club President"},
    "07_wrong_club_league.pdf": {"club": "Example FC", "country": "Kenya", "league": "Kenyan Premier League"},
    "08_closed_registration_window.pdf": {"registration": "Registration date: 2026-10-15"},
    "09_reused_template_harbor.pdf": {"sender": "Grace Njeri"},
    "10_reused_template_northgate.pdf": {
        "club": "Northgate Rovers",
        "sender": "Asha Mohamed",
        "email": "recruitment@northgaterovers.ke",
        "phone": "+254700000222",
        "signatory": "Asha Mohamed",
        "role": "Head of Youth Development",
    },
    "11_hard_negative_trial.pdf": {
        "trial": "Trial date: 2027-07-20",
        "arrival": "Arrival date: 2027-07-18",
        "departure": "Departure date: 2027-07-23",
        "fees": "No fees are requested. The player may arrange travel independently."
    },
    "12_hard_negative_precontract.pdf": {
        "registration": "Registration date: 2027-07-15",
        "status": "Player status: pre-contract agreed during final six months; registration after next window opens"
    },
    "13_hard_negative_international_contact.pdf": {
        "sender": "Morgan Example",
        "email": "morgan@global-football.example",
        "phone": "+442000000222",
        "fees": "No fees are requested from the player."
    },
    "14_metadata_anomaly.pdf": {"creator": "Synthetic Canva Export Tool"},
    "15_conflicting_travel_dates.pdf": {
        "arrival": "Arrival date: 2027-08-20",
        "trial": "Trial date: 2027-07-12",
        "departure": "Departure date: 2027-07-18",
    },
    "16_unknown_club_evidence.pdf": {
        "club": "Unlisted Sample Athletic",
        "country": "Exampleland",
        "league": "Example League",
        "sender": "Morgan Example",
        "email": "contact@unlisted-sample.example",
        "signatory": "Morgan Example",
        "role": "Recruitment Contact",
    },
}


def generate_documents(output_dir: Path = OUTPUT) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for filename, overrides in SCENARIOS.items():
        text, creator = make_letter(**overrides)
        document = fitz.open()
        page = document.new_page()
        page.insert_textbox(fitz.Rect(55, 55, 555, 760), text, fontsize=11, lineheight=1.35)
        document.set_metadata(
            {
                "title": filename.removesuffix(".pdf"),
                "author": "Synthetic OffsAid demo",
                "creator": creator,
                "producer": "PyMuPDF synthetic generator",
                "creationDate": "D:20261030120000Z",
            }
        )
        path = output_dir / filename
        document.save(path, garbage=4, deflate=True)
        document.close()
        generated.append(path)
    return generated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate clearly labeled synthetic OffsAid Step 2 PDFs.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    for generated_path in generate_documents(arguments.output):
        print(generated_path)