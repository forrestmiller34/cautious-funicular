import os
import sys

from sqlalchemy import create_engine, text

# Make sure Python can import nba_ingest.*
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "nba_ingest"))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from nba_ingest.config import load_settings  # uses your .env and DATABASE_URL


def main() -> None:
    settings = load_settings()
    engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")

    with engine.connect() as conn:
        print("Dropping schema public ...")
        conn.execute(text("DROP SCHEMA public CASCADE"))
        print("Recreating schema public ...")
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

    print("✅ Schema reset complete.")


if __name__ == "__main__":
    main()
