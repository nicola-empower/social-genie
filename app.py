# =====================================================
# FILE: app.py
# This version includes the full Gemini API prompts.
# =====================================================

import os
import psycopg2
import requests 
import re
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from dotenv import load_dotenv
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt

load_dotenv()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'a-super-secret-key-for-development')

# --- LOGIN SYSTEM SETUP ---
bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username FROM users WHERE id = %s;", (user_id,))
        user_data = cur.fetchone()
        cur.close()
        conn.close()
        if user_data:
            return User(id=user_data[0], username=user_data[1])
    return None

def get_db_connection():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

# --- AUTHENTICATION ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password FROM users WHERE username = %s;", (username,))
            user_data = cur.fetchone()
            cur.close()
            conn.close()
            if user_data and bcrypt.check_password_hash(user_data[2], password):
                user = User(id=user_data[0], username=user_data[1])
                login_user(user)
                return redirect(url_for('index'))
            else:
                flash('Invalid username or password.')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("INSERT INTO users (username, password) VALUES (%s, %s);", (username, hashed_password))
                conn.commit()
                cur.close()
                conn.close()
                flash('Registration successful! Please log in.')
                return redirect(url_for('login'))
            except psycopg2.IntegrityError:
                flash('Username already taken.')
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# --- PAGE ROUTES ---

@app.route('/')
@login_required
def index():
    posts = []
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT id, post_text, hashtags, created_at, scheduled_for, status FROM posts WHERE user_id = %s ORDER BY created_at DESC;", (current_user.id,))
        posts_data = cur.fetchall()
        cur.close()
        conn.close()
        for post_data in posts_data:
            posts.append({
                'id': post_data[0], 'text': post_data[1], 'hashtags': post_data[2], 
                'created_at': post_data[3], 'scheduled_for': post_data[4], 'status': post_data[5]
            })
    return render_template('index.html', posts=posts)

@app.route('/blog')
@login_required
def blog():
    blog_posts = []
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT id, title, created_at FROM blog_posts WHERE user_id = %s ORDER BY created_at DESC;", (current_user.id,))
        blog_posts_data = cur.fetchall()
        cur.close()
        conn.close()
        for blog_data in blog_posts_data:
            blog_posts.append({'id': blog_data[0], 'title': blog_data[1], 'created_at': blog_data[2]})
    return render_template('blog.html', blog_posts=blog_posts)

@app.route('/calendar')
@login_required
def calendar():
    return render_template('calendar.html')

@app.route('/blog/<int:blog_id>')
@login_required
def view_blog(blog_id):
    blog_post = None
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT id, title, content, created_at FROM blog_posts WHERE id = %s AND user_id = %s;", (blog_id, current_user.id))
        blog_data = cur.fetchone()
        cur.close()
        conn.close()
        if blog_data:
            blog_post = {
                'id': blog_data[0], 'title': blog_data[1],
                'content': blog_data[2], 'created_at': blog_data[3]
            }
    return render_template('view_blog.html', blog_post=blog_post)

@app.route('/labels')
@login_required
def labels():
    labels_list = []
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, color FROM labels WHERE user_id = %s ORDER BY name;", (current_user.id,))
        labels_data = cur.fetchall()
        cur.close()
        conn.close()
        for label_data in labels_data:
            labels_list.append({'id': label_data[0], 'name': label_data[1], 'color': label_data[2]})
    return render_template('labels.html', labels=labels_list)


# --- API AND ACTION ROUTES ---

@app.route('/api/posts')
@login_required
def api_posts():
    events = []
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.post_text, p.scheduled_for, l.color
            FROM posts p
            LEFT JOIN labels l ON p.label_id = l.id
            WHERE p.status = 'scheduled' AND p.scheduled_for IS NOT NULL AND p.user_id = %s;
        """, (current_user.id,))
        posts_data = cur.fetchall()
        cur.close()
        conn.close()
        for post_data in posts_data:
            events.append({
                'id': post_data[0],
                'title': post_data[1][:25] + '...', 
                'start': post_data[2].isoformat(),
                'url': url_for('edit', post_id=post_data[0]),
                'color': post_data[3] or '#ec4899'
            })
    return jsonify(events)

@app.route('/generate', methods=['POST'])
@login_required
def generate():
    user_prompt = request.form['prompt']
    api_key = os.getenv("GEMINI_API_KEY")
    
    # --- UPDATED PROMPT FOR LINKEDIN POSTS ---
    full_prompt = f"""
    Act as a "Systems Architect" and expert in business automation for small businesses. Your target audience is overwhelmed entrepreneurs who are looking to scale their operations and free up their time.
    Your task is to create 5 distinct LinkedIn posts based on the following topic: "{user_prompt}".
    Each post must adhere to the following structure and guidelines:
    - Tone: Professional, authoritative, and helpful, but with a human touch. Avoid overly technical jargon. Be friendly, informative, conversational, and engaging to encourage comments and reactions.
    - Structure:
      1. A compelling hook: Start with a question or a bold statement that addresses a common pain point of the target audience.
      2. A value-driven body: Use bullet points to provide actionable tips, a case study, or a "before and after" scenario.
      3. A clear call to action: End with a question to encourage engagement or an invitation to connect.
    - Content: The 5 posts should be unique and cover different angles of the topic. For example:
      - Post 1: A "why" post, explaining the strategic importance of the topic.
      - Post 2: A "how-to" post, with actionable tips.
      - Post 3: A case study, showcasing a real-world example.
      - Post 4: A myth-busting post, addressing a common misconception.
      - Post 5: A personal reflection, sharing your own experience with the topic.
    - Hashtags: Include 3-5 relevant hashtags at the end of each post, such as #businessautomation, #systemsarchitect, #entrepreneurship, #smallbusinessowner, #efficiency.
    Please provide the 5 LinkedIn posts in a clean, ready-to-post format, separated by '---'.
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        try:
            result = response.json()
            generated_text = result['candidates'][0]['content']['parts'][0]['text']
            individual_posts = generated_text.strip().split('---')
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                for post_content in individual_posts:
                    if post_content.strip():
                        parts = post_content.strip().split('\n')
                        # Find the last line that starts with '#' for hashtags
                        hashtag_line_index = -1
                        for i in range(len(parts) - 1, -1, -1):
                            if parts[i].strip().startswith('#'):
                                hashtag_line_index = i
                                break
                        
                        if hashtag_line_index != -1:
                            post_text = "\n".join(parts[:hashtag_line_index]).strip()
                            hashtags = " ".join(parts[hashtag_line_index:]).strip()
                        else: # Fallback if no hashtags are found
                            post_text = "\n".join(parts).strip()
                            hashtags = ""
                            
                        cur.execute("INSERT INTO posts (post_text, hashtags, status, user_id) VALUES (%s, %s, 'draft', %s);", (post_text, hashtags, current_user.id))
                conn.commit()
                cur.close()
                conn.close()
        except (KeyError, IndexError) as e:
            print(f"Error parsing Gemini response: {e}")
    return redirect(url_for('index'))

@app.route('/add_post', methods=['POST'])
@login_required
def add_post():
    post_text = request.form['post_text']
    hashtags = request.form['hashtags']
    if post_text and hashtags:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO posts (post_text, hashtags, status, user_id) VALUES (%s, %s, 'draft', %s);", (post_text, hashtags, current_user.id))
            conn.commit()
            cur.close()
            conn.close()
    return redirect(url_for('index'))

@app.route('/generate_blog', methods=['POST'])
@login_required
def generate_blog():
    user_prompt = request.form['blog_prompt']
    api_key = os.getenv("GEMINI_API_KEY")

    # --- UPDATED PROMPT FOR BLOG POSTS ---
    full_prompt = f"""
    Act as a "Systems Architect" and a leading expert in business process automation. You are writing for an audience of ambitious entrepreneurs who want to scale their businesses through smart, technology-driven solutions.
    Your task is to write a minimum 1500-word blog post on the following topic: "{user_prompt}".
    The blog post must meet the following criteria:
    - Structure and Content:
      - Engaging Introduction: Start with a relatable story or a startling statistic to hook the reader.
      - In-depth Analysis: The body of the post should provide a detailed, step-by-step strategy. Use clear subheadings to break up the text.
      - Evidence-Based Claims: Back up all claims with links to at least 3-5 external, reputable sources (e.g., industry reports, academic studies, articles from established business publications).
      - Actionable Advice: The post should be practical and provide the reader with a clear roadmap they can follow.
      - Strong Conclusion: Summarize the key takeaways and end with a powerful call to action.
    - Tone: Authoritative, well-researched, and highly credible. The tone should reflect your position as a premium technical consultant.
    
    After the blog post, please generate 5 unique LinkedIn posts based on the content of the article. These posts should follow the same structure and guidelines as the previous prompt (hook, value-driven body, CTA, and relevant hashtags).
    
    Please provide the full blog post and the 5 LinkedIn posts using the following strict format:
    <BLOG_TITLE_START>The Blog Title Goes Here<BLOG_TITLE_END>
    <BLOG_CONTENT_START>
    The entire blog content, with a minimum of 1500 words, goes here.
    <BLOG_CONTENT_END>
    <POST_START>
    LinkedIn Post 1 content...
    #hashtag1 #hashtag2
    <POST_END>
    <POST_START>
    LinkedIn Post 2 content...
    #hashtag3 #hashtag4
    <POST_END>
    <POST_START>
    LinkedIn Post 3 content...
    #hashtag5 #hashtag6
    <POST_END>
    <POST_START>
    LinkedIn Post 4 content...
    #hashtag7 #hashtag8
    <POST_END>
    <POST_START>
    LinkedIn Post 5 content...
    #hashtag9 #hashtag10
    <POST_END>
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        try:
            result = response.json()
            generated_text = result['candidates'][0]['content']['parts'][0]['text']
            title_match = re.search(r'<BLOG_TITLE_START>(.*?)<BLOG_TITLE_END>', generated_text, re.DOTALL)
            content_match = re.search(r'<BLOG_CONTENT_START>(.*?)<BLOG_CONTENT_END>', generated_text, re.DOTALL)
            linkedin_posts = re.findall(r'<POST_START>(.*?)<POST_END>', generated_text, re.DOTALL)
            if title_match and content_match and linkedin_posts:
                blog_title = title_match.group(1).strip()
                blog_content = content_match.group(1).strip()
                conn = get_db_connection()
                if conn:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO blog_posts (title, content, user_id) VALUES (%s, %s, %s) RETURNING id;", (blog_title, blog_content, current_user.id))
                    blog_id = cur.fetchone()[0]
                    for post_content in linkedin_posts:
                        if post_content.strip():
                            # Find the last line that starts with '#' for hashtags
                            parts = post_content.strip().split('\n')
                            hashtag_line_index = -1
                            for i in range(len(parts) - 1, -1, -1):
                                if parts[i].strip().startswith('#'):
                                    hashtag_line_index = i
                                    break
                            
                            if hashtag_line_index != -1:
                                post_text = "\n".join(parts[:hashtag_line_index]).strip()
                                hashtags = " ".join(parts[hashtag_line_index:]).strip()
                            else: # Fallback if no hashtags are found
                                post_text = "\n".join(parts).strip()
                                hashtags = ""

                            cur.execute("INSERT INTO posts (post_text, hashtags, status, blog_post_id, user_id) VALUES (%s, %s, 'draft', %s, %s);", (post_text, hashtags, blog_id, current_user.id))
                    conn.commit()
                    cur.close()
                    conn.close()
            else:
                print("Error: Could not find all required separators in the AI response.")
        except (KeyError, IndexError) as e:
            print(f"Error parsing Gemini response for blog: {e}")
    return redirect(url_for('blog'))

@app.route('/update_blog/<int:blog_id>', methods=['POST'])
@login_required
def update_blog(blog_id):
    title = request.form['title']
    content = request.form['content']
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("UPDATE blog_posts SET title = %s, content = %s WHERE id = %s AND user_id = %s;", (title, content, blog_id, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for('view_blog', blog_id=blog_id))

# --- ROUTES FOR ADDING AND DELETING LABELS ---
@app.route('/add_label', methods=['POST'])
@login_required
def add_label():
    """Adds a new label to the database for the current user."""
    name = request.form['label_name']
    color = request.form['label_color']
    if name and color:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO labels (name, color, user_id) VALUES (%s, %s, %s);", (name, color, current_user.id))
            conn.commit()
            cur.close()
            conn.close()
    return redirect(url_for('labels'))

@app.route('/delete_label/<int:label_id>', methods=['POST'])
@login_required
def delete_label(label_id):
    """Deletes a label from the database."""
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("UPDATE posts SET label_id = NULL WHERE label_id = %s AND user_id = %s;", (label_id, current_user.id))
        cur.execute("DELETE FROM labels WHERE id = %s AND user_id = %s;", (label_id, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for('labels'))


@app.route('/schedule/<int:post_id>', methods=['POST'])
@login_required
def schedule(post_id):
    scheduled_time_str = request.form['schedule_time']
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("UPDATE posts SET scheduled_for = %s, status = 'scheduled' WHERE id = %s AND user_id = %s;", (scheduled_time_str, post_id, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for('index'))

@app.route('/edit/<int:post_id>')
@login_required
def edit(post_id):
    post = None
    labels_list = []
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT id, post_text, hashtags, scheduled_for, label_id FROM posts WHERE id = %s AND user_id = %s;", (post_id, current_user.id))
        post_data = cur.fetchone()
        cur.execute("SELECT id, name FROM labels WHERE user_id = %s ORDER BY name;", (current_user.id,))
        labels_data = cur.fetchall()
        for label_data in labels_data:
            labels_list.append({'id': label_data[0], 'name': label_data[1]})
        cur.close()
        conn.close()
        if post_data:
            post = {'id': post_data[0], 'text': post_data[1], 'hashtags': post_data[2], 'scheduled_for': post_data[3], 'label_id': post_data[4]}
    return render_template('edit.html', post=post, labels=labels_list)

@app.route('/update/<int:post_id>', methods=['POST'])
@login_required
def update(post_id):
    post_text = request.form['post_text']
    hashtags = request.form['hashtags']
    scheduled_time_str = request.form['schedule_time']
    label_id = request.form.get('label_id')
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        label_id_to_save = label_id if label_id else None
        if scheduled_time_str:
            cur.execute("""
                UPDATE posts SET post_text = %s, hashtags = %s, scheduled_for = %s, status = 'scheduled', label_id = %s
                WHERE id = %s AND user_id = %s;
            """, (post_text, hashtags, scheduled_time_str, label_id_to_save, post_id, current_user.id))
        else:
            cur.execute("""
                UPDATE posts SET post_text = %s, hashtags = %s, scheduled_for = NULL, status = 'draft', label_id = %s
                WHERE id = %s AND user_id = %s;
            """, (post_text, hashtags, label_id_to_save, post_id, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for('index'))

@app.route('/delete/<int:post_id>', methods=['POST'])
@login_required
def delete(post_id):
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM posts WHERE id = %s AND user_id = %s;", (post_id, current_user.id))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
