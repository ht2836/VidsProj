import os
import requests
import psycopg2
from youtube_transcript_api import YouTubeTranscriptApi
from huggingface_hub import InferenceClient # <--- NEW IMPORT

# 1. SETUP & CONFIG
DB_URI = os.environ["DB_URI"]
HF_TOKEN = os.environ["HF_TOKEN"]
IMGBB_KEY = os.environ["IMGBB_KEY"]
WP_USER = os.environ["WP_USER"]
WP_PASS = os.environ["WP_PASS"]
WP_URL = "https://your-site.com/wp-json/wp/v2/posts"

# Initialize the Client (Handles all URLs automatically)
client = InferenceClient(token=HF_TOKEN)

def generate_text_hf(prompt):
    """
    Uses Mistral-7B-Instruct-v0.3 via the Chat Completion API.
    This method is much more stable than the old raw HTTP request.
    """
    messages = [
        {"role": "user", "content": prompt}
    ]
    
    try:
        response = client.chat_completion(
            model="mistralai/Mistral-7B-Instruct-v0.3",
            messages=messages,
            max_tokens=1500,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Text Gen Error: {e}")
        return None

def generate_image_hf(prompt):
    """
    Uses Stable Diffusion XL directly via the Python client.
    Returns raw bytes.
    """
    try:
        image = client.text_to_image(
            prompt=prompt,
            model="stabilityai/stable-diffusion-xl-base-1.0"
        )
        
        # The client returns a PIL Image object, we need bytes for ImgBB
        import io
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        return img_byte_arr.getvalue()
    except Exception as e:
        print(f"Image Gen Error: {e}")
        return None

def upload_imgbb(image_binary):
    if not image_binary: return None
    payload = {"key": IMGBB_KEY}
    files = {"image": image_binary}
    try:
        res = requests.post("https://api.imgbb.com/1/upload", data=payload, files=files)
        return res.json()['data']['url']
    except Exception as e:
        print(f"ImgBB Error: {e}")
        return None

def main():
    # ... (Database connection logic remains exactly the same as before) ...
    conn = psycopg2.connect(DB_URI)
    cur = conn.cursor()
    cur.execute("SELECT id, title, url FROM videos WHERE status='pending' LIMIT 1")
    row = cur.fetchone()
    
    if not row:
        print("No pending videos.")
        return

    vid_id, title, url = row
    print(f"Processing: {title}")

    # Transcript Logic (Same as before)
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
    except:
        cur.execute("UPDATE videos SET status='error' WHERE id=%s", (vid_id,))
        conn.commit()
        print("Transcript failed.")
        return

    # GENERATION
    print("Generating Blog...")
    blog_prompt = f"Write a detailed, SEO-friendly blog post with HTML formatting (h2, h3, p) about: {title}. Context: {transcript_text[:4000]}..."
    blog_content = generate_text_hf(blog_prompt)
    
    if not blog_content:
        print("Failed to generate text.")
        return

    print("Generating Image...")
    img_prompt = f"Editorial style thumbnail for article about {title}, 4k, high quality"
    img_binary = generate_image_hf(img_prompt)
    img_url = upload_imgbb(img_binary)

    # WP PUBLISH (Same as before)
    print("Publishing to WordPress...")
    wp_data = {
        "title": title,
        "content": blog_content,
        "status": "publish",
        "fifu_image_url": img_url, 
        "fifu_image_alt": title
    }
    
    wp_res = requests.post(WP_URL, json=wp_data, auth=(WP_USER, WP_PASS))

    if wp_res.status_code == 201:
        cur.execute("UPDATE videos SET status='published' WHERE id=%s", (vid_id,))
        conn.commit()
        print("SUCCESS: Published!")
    else:
        print("WP Error:", wp_res.text)

if __name__ == "__main__":
    main()