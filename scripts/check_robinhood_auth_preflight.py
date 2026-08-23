import os
from pathlib import Path


RAW_SESSION = Path.home() / ".tokens" / "robinhood.pickle"
REQUIRED_ENV = ("ROBINHOOD_USERNAME", "ROBINHOOD_PASSWORD", "ROBINHOOD_SESSION_KEY")


def github_error(title, message):
    print(f"::error title={title}::{message}")


def github_warning(title, message):
    print(f"::warning title={title}::{message}")


def github_notice(title, message):
    print(f"::notice title={title}::{message}")


def main():
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        github_error(
            "Robinhood secrets missing",
            "Missing required secret(s): " + ", ".join(missing),
        )
        raise SystemExit(1)

    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    is_scheduled = event_name == "schedule"
    if RAW_SESSION.exists() and RAW_SESSION.stat().st_size > 0:
        github_notice(
            "Robinhood session restored",
            f"Cached session file is present at {RAW_SESSION}. Robinhood may still require app approval if it expired.",
        )
        return

    message = (
        "No cached Robinhood session was restored. The notebook may need live app "
        "verification during login. For scheduled runs this should be fixed by refreshing "
        "the encrypted session cache before relying on automation."
    )
    if is_scheduled:
        github_error("Robinhood session cache missing", message)
        raise SystemExit(1)
    github_warning("Robinhood session cache missing", message)


if __name__ == "__main__":
    main()

