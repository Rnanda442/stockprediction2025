import argparse
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


ROOT = Path(__file__).resolve().parents[1]
RAW_SESSION = Path.home() / ".tokens" / "robinhood.pickle"
ENCRYPTED_SESSION = ROOT / ".auth-cache" / "robinhood.pickle.fernet"
KEY_ENV = "ROBINHOOD_SESSION_KEY"


def cipher():
    value = os.getenv(KEY_ENV, "").strip()
    if not value:
        print(f"{KEY_ENV} is not configured; encrypted Robinhood session reuse is disabled.")
        return None
    try:
        return Fernet(value.encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{KEY_ENV} is not a valid Fernet key") from exc


def restore():
    fernet = cipher()
    if fernet is None or not ENCRYPTED_SESSION.exists():
        print("No encrypted Robinhood session was restored.")
        return
    try:
        plaintext = fernet.decrypt(ENCRYPTED_SESSION.read_bytes())
    except InvalidToken as exc:
        raise RuntimeError("Encrypted Robinhood session could not be decrypted") from exc
    RAW_SESSION.parent.mkdir(parents=True, exist_ok=True)
    RAW_SESSION.write_bytes(plaintext)
    try:
        RAW_SESSION.chmod(0o600)
    except OSError:
        pass
    print(f"Restored encrypted Robinhood session to {RAW_SESSION}.")


def save():
    fernet = cipher()
    if fernet is None or not RAW_SESSION.exists():
        print("No Robinhood session was available to encrypt.")
        return
    ENCRYPTED_SESSION.parent.mkdir(parents=True, exist_ok=True)
    ENCRYPTED_SESSION.write_bytes(fernet.encrypt(RAW_SESSION.read_bytes()))
    print(f"Saved encrypted Robinhood session to {ENCRYPTED_SESSION}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("restore", "save"))
    args = parser.parse_args()
    if args.mode == "restore":
        restore()
    else:
        save()


if __name__ == "__main__":
    main()
