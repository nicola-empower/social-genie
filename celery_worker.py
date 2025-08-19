# =====================================================
# FILE: celery_worker.py
# This version fixes the Windows connection issue.
# =====================================================

# --- THIS IS THE FIX ---
# This line must be the very first thing that runs.
import eventlet
eventlet.monkey_patch()
# --- END OF FIX ---

import os
import psycopg2
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv
from linkedin_api import Linkedin

# Load environment variables from the .env file
load_dotenv()

# --- CELERY CONFIGURATION (UPDATED) ---
# We've changed 'localhost' to '127.0.0.1' to fix a Windows issue.
celery_app = Celery(
    'tasks',
    broker='redis://127.0.0.1:6379/0',
    backend='redis://127.0.0.1:6379/0'
)

# --- DATABASE CONNECTION FUNCTION ---
def get_db_connection():
    """Establishes a connection to the database."""
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        return conn
    except Exception as e:
        print(f"WORKER: Error connecting to database: {e}")
        return None

# --- THE MAIN BACKGROUND TASK ---
@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    """Sets up the scheduled task to run every minute."""
    sender.add_periodic_task(60.0, check_and_post_scheduled_content.s(), name='check for posts every minute')

@celery_app.task
def check_and_post_scheduled_content():
    """
    This is the main task. It fetches due posts, posts them to LinkedIn,
    and updates their status in the database.
    """
    print("WORKER: Checking for scheduled posts...")
    conn = get_db_connection()
    if not conn:
        return

    cur = conn.cursor()
    cur.execute("""
        SELECT id, post_text, hashtags, user_id FROM posts
        WHERE status = 'scheduled' AND scheduled_for <= NOW();
    """)
    posts_to_publish = cur.fetchall()
    
    if not posts_to_publish:
        print("WORKER: No posts due for publishing.")
        cur.close()
        conn.close()
        return

    print(f"WORKER: Found {len(posts_to_publish)} post(s) to publish.")

    linkedin_username = os.getenv("LINKEDIN_USERNAME")
    linkedin_password = os.getenv("LINKEDIN_PASSWORD")

    if not linkedin_username or not linkedin_password:
        print("WORKER: LinkedIn credentials not found in .env file.")
        cur.close()
        conn.close()
        return

    try:
        api = Linkedin(linkedin_username, linkedin_password)
        print("WORKER: Successfully authenticated with LinkedIn.")
    except Exception as e:
        print(f"WORKER: LinkedIn authentication failed: {e}")
        cur.close()
        conn.close()
        return

    for post_data in posts_to_publish:
        post_id, post_text, hashtags, user_id = post_data
        full_post_content = f"{post_text}\n\n{hashtags}"

        try:
            post_result = api.create_share(commentary=full_post_content, visibility='CONNECTIONS')
            post_urn = post_result.get('urn')
            
            if post_urn:
                print(f"WORKER: Successfully posted post ID {post_id} to LinkedIn.")
                cur.execute(
                    "UPDATE posts SET status = 'posted', linkedin_post_urn = %s WHERE id = %s;",
                    (post_urn, post_id)
                )
                conn.commit()
            else:
                print(f"WORKER: Failed to get post URN for post ID {post_id}. Post may have failed.")

        except Exception as e:
            print(f"WORKER: Error posting post ID {post_id} to LinkedIn: {e}")

    cur.close()
    conn.close()
