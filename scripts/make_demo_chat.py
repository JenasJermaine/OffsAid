"""Write data/demo/agent_judith_chat.png: an INVENTED WhatsApp chat for the live demo's @message-check.

The same agent as the Lakeview United letter (scripts/make_demo_letter.py): Judith Otieno, on the
same WhatsApp number (+254 999 000 006) and M-Pesa till (9990002), so the two demo samples tell one
story. Numbers use the invalid prefix +254 999; no real person or club.

    python scripts/make_demo_chat.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_test_letters import _chat_screenshot  # noqa: E402

OUT = ROOT / "data" / "demo" / "agent_judith_chat.png"

CHAT = {
    "contact": "Agent Judith", "number": "+254 999 000 006",
    "messages": [
        ("in", "Hello Brian, this is Judith Otieno from Lakeview United FC.", "10:01"),
        ("in", "Congratulations! You have been selected for our senior academy squad. Training starts 5 January.", "10:02"),
        ("in", "Visa, flights and accommodation are already sorted on our side.", "10:02"),
        ("in", "You only need to pay KES 25,000 registration to M-Pesa till 9990002 by tomorrow 5pm.", "10:03"),
        ("in", "After that the slot goes to the next player. Don't tell your current coach until you have travelled.", "10:03"),
        ("out", "Okay, can I pay half now?", "10:11"),
        ("in", "No. Full amount, other boys are waiting.", "10:12"),
    ],
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(_chat_screenshot(CHAT))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
