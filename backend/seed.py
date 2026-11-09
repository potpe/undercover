"""Reseed UnderCover's selected database from the approved business data."""

from dotenv import load_dotenv

from backend.database import reseed_database


def main() -> None:
    load_dotenv(override=True)
    reseed_database()
    print("Seeded 25 waiting claims.")


if __name__ == "__main__":
    main()
