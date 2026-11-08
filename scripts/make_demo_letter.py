"""Write data/demo/lakeview_united_offer.pdf: an INVENTED offer letter for the live demo.

It reuses details from the simulated linking seeds under a new club name, so cross-case linking has
something to find: the agent's WhatsApp number is sim-01's (Coast City FC) and the M-Pesa till is
sim-02's (Harbour Town FC). Numbers use the invalid prefix +254 999; no real person or club.

    python scripts/make_demo_letter.py
"""
from pathlib import Path

import fitz

OUT = Path(__file__).resolve().parents[1] / "data" / "demo" / "lakeview_united_offer.pdf"

HTML = """
<h2 style="color:#0b4f6c;margin:0">LAKEVIEW UNITED FC</h2>
<p style="margin:0;color:#555">Football Academy &middot; Kisumu, Kenya</p>
<hr/>
<p>12 November 2026</p>
<p>Dear Player,</p>
<p>Following your trial video, Lakeview United FC is pleased to offer you a place in our senior academy
squad for the 2027 season, starting on 5 January 2027. Your visa, flights and accommodation will be
arranged by the club.</p>
<p>To confirm your place you must pay a registration fee of KES 25,000 to M-Pesa till number 9990002
within 3 days of this letter. Places are limited and will be given to other players after that date.</p>
<p>For any questions contact our agent Ms Otieno on WhatsApp +254 999 000 001.</p>
<p>Yours sincerely,</p>
<p><b>Ms Otieno</b><br/>Head of Recruitment, Lakeview United FC</p>
<p style="font-size:8px;color:#999">Simulated document for the OffsAid demo. Invented club, people and numbers.</p>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(0, 0, 595, 18), color=None, fill=(0.04, 0.31, 0.42))
    page.insert_htmlbox(fitz.Rect(60, 50, 535, 800), HTML, css="* {font-family: sans-serif; font-size: 11px;}")
    doc.set_metadata({"title": "Offer letter", "author": "Lakeview United FC"})
    doc.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
