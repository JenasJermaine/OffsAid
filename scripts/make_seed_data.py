"""Generate the simulated seed cases: seed_data/seed_cases.json and seed_data/letters/*.png

All names, clubs, numbers and emails are MADE UP. The phone numbers use the invalid prefix +254 999 so they can
never belong to a real person. Every row has source "simulated" and must be shown as simulated on screen.

Run:  python make_seed_data.py [output_dir]
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# six distinct page layouts: (rectangles as x0, y0, x1, y1, grey level) and the letter text for each
LAYOUTS = {
    "L1": {"rects": [(0, 0, 595, 110, 40), (40, 150, 555, 160, 120), (40, 700, 300, 820, 200)],
           "text": ("Dear {player}, On behalf of {club} we are pleased to invite you for a trial from {date}. To confirm your "
                    "registration and secure your place you must pay a registration fee of KES {amt} to our agent before the trial. "
                    "Your visa and travel will be arranged by the club. Yours sincerely, {agent}, Director of Football, {club}.")},
    "L2": {"rects": [(0, 0, 150, 842, 60), (200, 40, 560, 90, 150), (200, 760, 560, 800, 100)],
           "text": ("Official offer of contract. {club} offers {player} a professional contract starting {date}. The club will cover "
                    "your accommodation and flights. A clearance fee of KES {amt} is payable to the club agent {agent} to release "
                    "your documents. Signed, {agent} for {club}.")},
    "L3": {"rects": [(0, 380, 595, 460, 90), (60, 40, 200, 140, 170), (400, 640, 560, 800, 60)],
           "text": ("Trial invitation. {club} invites {player} to its open trial on {date}. Bring boots, ID and your medical report. "
                    "There is no fee for the trial. Contact {agent} at the club office. {club}.")},
    "L4": {"rects": [(0, 0, 595, 60, 30), (0, 782, 595, 842, 30), (260, 200, 340, 600, 140)],
           "text": ("Congratulations {player}. {club} has selected you for its academy intake of {date}. Your place is guaranteed once "
                    "you send KES {amt} to the agent {agent}. Do not share this letter. {club}.")},
    "L5": {"rects": [(30, 30, 565, 80, 110), (30, 300, 300, 330, 60), (320, 500, 565, 760, 180)],
           "text": ("Letter of intent. {club} intends to register {player} for the season starting {date}. Registration is handled "
                    "by the club directly with the federation and the player pays nothing. Regards, {agent}, {club}.")},
    "L6": {"rects": [(0, 200, 595, 240, 70), (100, 600, 500, 650, 130), (20, 20, 80, 120, 200)],
           "text": ("Trial confirmation from {club}. {player}, your trial is on {date} at the club ground at 9am. Training kit is "
                    "provided by the club. Please confirm by email. {agent}, {club}.")},
}

WORDING = {
    "W1": ("Congratulations, you have been selected for our academy. To confirm your slot send KES 15,000 registration fee to my "
           "number today. Your visa is already arranged. Do not tell anyone about this."),
    "W2": ("Good news. The coach has already picked you. Send the registration fee to my M-Pesa number before Friday and we "
           "book your ticket. Keep this between us."),
    "W3": "Hello, we saw your video. Please send your date of birth and position and our scout will reply by email.",
    "W4": "Your trial is at 9am on Saturday. Bring boots and ID. There is no fee.",
}

# (id, club, agent, phone, account, email, layout, wording, labels, status)
CASES = [
    ("sim-01", "Coast City FC", "Mr Kamau", "0999 000 001", "9990001", "kamau.scout@coastcity-fc.example", "L1", "W1",
     ["advance_fee_request", "guaranteed_outcome", "visa_travel_arranged", "secrecy"], "unreviewed"),
    ("sim-02", "Harbour Town FC", "Ms Mwende", "+254 999 000 002", "9990002", "mwende.fc@gmail.com", "L2", "W3", [], "unreviewed"),
    ("sim-03", "Lakeside Stars", "Mr Odhiambo", "0999 000 003", "9990003", None, "L3", "W3", [], "unreviewed"),
    ("sim-04", "Northern Lions", "Mr Wekesa", "0999 000 004", "9990004", "wekesa@northernlions.example", "L4", "W2",
     ["advance_fee_request", "guaranteed_outcome", "urgency_pressure", "secrecy"], "unreviewed"),
    ("sim-05", "Riverbend Rovers", "Ms Wafula", "0999 000 005", None, "wafula@riverbend-rovers.example", "L5", "W4", [], "verified_genuine"),
    ("sim-06", "Gold Coast Academy", "Mr Otieno", "0999 000 006", "9990006", None, "L1", "W2",
     ["advance_fee_request", "guaranteed_outcome", "urgency_pressure", "secrecy"], "confirmed_scam"),
    ("sim-07", "Riverside FC", "Mr Njoroge", "0999 000 007", None, "njoroge@riverside-fc.example", "L6", "W4", [], "verified_genuine"),
    ("sim-08", "Hilltop Stars", "Ms Chebet", "0999 000 008", "9990008", None, None, "W3", [], "unreviewed"),
    ("sim-09", "Delta City", "Mr Abdi", "0999 000 009", None, "abdi@deltacity.example", None, "W3", [], "unreviewed"),
    ("sim-10", "Sunrise FC", "Ms Atieno", "0999 000 010", "9990010", None, None, "W3", [], "unreviewed"),
]


def letter_text(layout: str, club: str, agent: str, player: str = "the player", date: str = "12 March 2026", amt: str = "15,000") -> str:
    return LAYOUTS[layout]["text"].format(club=club, agent=agent, player=player, date=date, amt=amt)


def render_letter(layout: str, club: str, path: Path) -> None:
    """A simple page image: grey blocks at the layout's positions, plus the club name in the header."""
    img = Image.new("L", (595, 842), 255)
    d = ImageDraw.Draw(img)
    for x0, y0, x1, y1, g in LAYOUTS[layout]["rects"]:
        d.rectangle((x0, y0, x1, y1), fill=g)
    d.text((60, 20), club, fill=255 if layout in ("L1",) else 0)
    img.save(path)


def main(out: str = "seed_data") -> None:
    out_dir = Path(out)
    (out_dir / "letters").mkdir(parents=True, exist_ok=True)
    cases = []
    for cid, club, agent, phone, account, email, layout, w, labels, status in CASES:
        img = f"letters/{cid}.png" if layout else None
        if layout:
            render_letter(layout, club, out_dir / img)
        idents = [{"kind": "phone", "value": phone, "role": "agent_contact"}]
        if account:
            idents.append({"kind": "account", "value": account, "role": "payment_to"})
        if email:
            idents.append({"kind": "email", "value": email, "role": "sender"})
        cases.append({"external_ref": f"seed:{cid}", "source": "simulated", "status": status, "claimed_club": club,
                      "claimed_agent": agent, "identifiers": idents, "wording_text": WORDING[w], "wording_labels": labels,
                      "summary": {"note": "simulated case, invented data"}}
                     | ({"letter_image": img, "letter_text": letter_text(layout, club, agent)} if layout else {}))
    (out_dir / "seed_cases.json").write_text(json.dumps({"cases": cases}, indent=2, ensure_ascii=False))
    print(f"wrote {len(cases)} simulated cases and letter images to {out_dir}/")


if __name__ == "__main__":
    main(*sys.argv[1:2])
