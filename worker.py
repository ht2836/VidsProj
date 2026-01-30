import os
import requests
import psycopg2
from youtube_transcript_api import YouTubeTranscriptApi

# 1. SETUP & CONFIG (Load from Environment Variables)
DB_URI = os.environ["DB_URI"]
HF_TOKEN = os.environ["HF_TOKEN"] # Hugging Face Token
IMGBB_KEY = os.environ["IMGBB_KEY"]
WP_USER = os.environ["WP_USER"]
WP_PASS = os.environ["WP_PASS"] # Application Password
WP_URL = "https://your-site.com/wp-json/wp/v2/posts"

# APIs
HF_API_URL_TEXT = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"
HF_API_URL_IMG = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"

def generate_text_hf(prompt):
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 1500, "return_full_text": False}
    }
    response = requests.post(HF_API_URL_TEXT, headers=headers, json=payload)
    return response.json()[0]['generated_text']

def generate_image_hf(prompt):
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    response = requests.post(HF_API_URL_IMG, headers=headers, json={"inputs": prompt})
    return response.content # Returns binary image data

def upload_imgbb(image_binary):
    payload = {"key": IMGBB_KEY}
    files = {"image": image_binary}
    res = requests.post("https://api.imgbb.com/1/upload", data=payload, files=files)
    return res.json()['data']['url']

def main():
    # A. Connect to DB and fetch 1 pending video
    conn = psycopg2.connect(DB_URI)
    cur = conn.cursor()
    cur.execute("SELECT id, title, url FROM videos WHERE status='pending' LIMIT 1")
    row = cur.fetchone()
    
    if not row:
        print("No pending videos.")
        return

    vid_id, title, url = row
    print(f"Processing: {title}")

    # B. Get Transcript
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
    except:
        # If no transcript, mark error and skip
        cur.execute("UPDATE videos SET status='error' WHERE id=%s", (vid_id,))
        conn.commit()
        return

    # C. Generate Blog
    blog_prompt = f"Write a detailed, SEO-friendly blog post about: {title}. Context: {transcript_text[:4000]}..."
    blog_content = generate_text_hf(blog_prompt)

    # D. Generate Image
    img_prompt = f"Editorial style thumbnail for article about {title}, 4k, high quality"
    img_binary = generate_image_hf(img_prompt)
    img_url = upload_imgbb(img_binary)

    # E. Publish to WordPress
    wp_data = {
        "title": title,
        "content": blog_content,
        "status": "publish",
        "fifu_image_url": img_url,
        "fifu_image_alt": title
    }
    wp_res = requests.post(
        WP_URL, 
        json=wp_data, 
        auth=(WP_USER, WP_PASS)
    )

    # F. Update DB
    if wp_res.status_code == 201:
        cur.execute("UPDATE videos SET status='published' WHERE id=%s", (vid_id,))
        conn.commit()
        print("Published successfully!")
    else:
        print("WP Error:", wp_res.text)

if __name__ == "__main__":
    main()
