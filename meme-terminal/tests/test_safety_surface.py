"""The package must not grow a signing or order path."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"

FORBIDDEN = (
    "sendTransaction",
    "signTransaction",
    "sign_transaction",
    "Keypair(",
    "solders",
    "solana-py",
    "private_key",
    "secret_key",
    "from solana",
)


def test_source_has_no_signing_surface():
    text = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*.py"))
    for marker in FORBIDDEN:
        assert marker not in text, marker


def test_optional_intel_is_not_fetched():
    """StalkChain, FOMO, X, and Padre are payload slots. The package does not call them."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*.py"))
    for marker in ("stalkchain.com", "api.fomo", "padre.gg", "api.x.com", "api.twitter.com"):
        assert marker not in text, marker
