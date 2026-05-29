"""Run once to create all tables in Supabase."""
from dotenv import load_dotenv
load_dotenv()
import db
db.init_db()
print("Tables created successfully in Supabase.")