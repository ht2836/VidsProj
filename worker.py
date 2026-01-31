import os
import requests
from supabase import create_client, Client
from youtube_transcript_api import YouTubeTranscriptApi
from huggingface_hub import InferenceClient
import io

# 1. SETUP & CONFIG
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
HF_TOKEN = os.environ["HF_TOKEN"]
IMGBB_KEY = os.environ["IMGBB_KEY"]
WP_USER = os.environ["WP_USER"]
WP_PASS = os.environ["WP_PASS"]
WP_URL = "https://test.harshtrivedi.in/wp-json/wp/v2/posts" # UPDATE THIS WITH YOUR DOMAIN

# Initialize Supabase (API Mode)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize AI Client
hf_client = InferenceClient(token=HF_TOKEN)

def generate_text_hf(prompt):
    messages = [{"role": "user", "content": prompt}]
    try:
        response = hf_client.chat_completion(
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
    try:
        image = hf_client.text_to_image(
            prompt=prompt,
            model="stabilityai/stable-diffusion-xl-base-1.0"
        )
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
    print("Connecting to Supabase via API...")
    
    # A. Fetch 1 pending video using API
    # Equivalent to: SELECT * FROM videos WHERE status='pending' LIMIT 1
    response = supabase.table("videos").select("id, title, url").eq("status", "pending").limit(1).execute()
    
    if not response.data:
        print("No pending videos found.")
        return

    video = response.data[0]
    vid_id = video['id']
    title = video['title']
    print(f"Processing: {title}")

    # B. Get Transcript
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
    except Exception as e:
        print(f"Transcript Error: {e}")
        # Mark as error in DB
        supabase.table("videos").update({"status": "error"}).eq("id", vid_id).execute()
        return

    # C. Generate Content
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

    # D. Publish to WordPress
    print("Publishing to WordPress...")
    wp_data = {
        "title": title,
        "content": blog_content,
        "status": "publish",
        "fifu_image_url": img_url, 
        "fifu_image_alt": title
    }
    
    # Note: Use basic auth for WP Application Passwords
    wp_res = requests.post(WP_URL, json=wp_data, auth=(WP_USER, WP_PASS))

    if wp_res.status_code == 201:
        # E. Update DB to 'published'
        supabase.table("videos").update({"status": "published"}).eq("id", vid_id).execute()
        print("SUCCESS: Published!")
    else:
        print(f"WP Error {wp_res.status_code}: {wp_res.text}")

if __name__ == "__main__":
    main()