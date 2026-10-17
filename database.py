import json
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing from .env")

    return psycopg.connect(database_url)


# 1. Create the table schema
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS family_members (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL,
    relationship VARCHAR(100),
    email VARCHAR(100),
    phone VARCHAR(20),
    location VARCHAR(100),
    bio TEXT,
    avatar TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    joined_at TIMESTAMPTZ,
    notify JSONB,
    password VARCHAR(255)
);
"""

# 2. Insert query matching JSON keys
INSERT_MEMBER_SQL = """
INSERT INTO family_members (
    username, name, role, relationship, email, phone, location, 
    bio, avatar, is_active, joined_at, notify, password
) VALUES (
    %(username)s, %(name)s, %(role)s, %(relationship)s, %(email)s, %(phone)s, %(location)s, 
    %(bio)s, %(avatar)s, %(is_active)s, %(joined_at)s, %(notify)s, %(password)s
)
ON CONFLICT (username) DO NOTHING;
"""


def seed_family_data(json_filepath: str = "family_data.json"):
    # Load JSON file
    with open(json_filepath, "r", encoding="utf-8") as file:
        family_data = json.load(file)

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Step A: Ensure table exists
            cursor.execute(CREATE_TABLE_SQL)

            # Step B: Insert family records
            for username, member in family_data.items():
                # Convert the nested 'notify' dictionary into a JSON string for JSONB column
                member["notify"] = json.dumps(member["notify"])

                cursor.execute(INSERT_MEMBER_SQL, member)

        # Commit the transaction so changes are saved
        conn.commit()
        print(
            f"Successfully seeded {len(family_data)} family members into PostgreSQL!"
        )


if __name__ == "__main__":
    # Make sure 'family_data.json' is in the same folder or update path here
    seed_family_data("family_data.json")