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
WP_URL = "https://test.harshtrivedi.in/wp-json/wp/v2/posts" 

# Initialize Clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
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
        print(f"AI Text Gen Error: {e}")
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
        print(f"AI Image Gen Error: {e}")
        return None

def upload_imgbb(image_binary):
    if not image_binary: return None
    payload = {"key": IMGBB_KEY}
    files = {"image": image_binary}
    try:
        res = requests.post("https://api.imgbb.com/1/upload", data=payload, files=files)
        if res.status_code == 200:
            return res.json()['data']['url']
        else:
            print(f"ImgBB Upload Failed: {res.text}")
            return None
    except Exception as e:
        print(f"ImgBB Error: {e}")
        return None

def main():
    print("Connecting to Supabase...")
    
    # A. Fetch 1 pending video (Select ALL columns to get description)
    response = supabase.table("videos").select("*").eq("status", "pending").limit(1).execute()
    
    if not response.data:
        print("No pending videos found.")
        return

    video = response.data[0]
    vid_id = video['id']
    title = video['title']
    # Safe fetch for description, defaulting to empty string if None
    description = video.get('description', '') or "No description provided."
    
    print(f"Processing: {title}")

    # B. Get Transcript (WITH FALLBACK LOGIC)
    transcript_text = ""
    try:
        print("Attempting to fetch transcript...")
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
        print("Transcript fetched successfully!")
    except Exception as e:
        print(f"Transcript unavailable ({e}). Using Description instead.")
        transcript_text = f"This video does not have a transcript. Description: {description}"

    # C. Generate Blog Content
    print("Generating Blog via Hugging Face...")
    blog_prompt = f"""
    You are an expert blogger. Write a detailed, engaging, SEO-friendly blog post about this video.
    
    Video Title: {title}
    Context Information: {transcript_text[:4000]}
    
    IMPORTANT: Return the response in HTML format (use <h2>, <p> tags). Do not use Markdown (no ** or ##).
    """
    
    blog_content = generate_text_hf(blog_prompt)
    
    if not blog_content:
        print("Failed to generate text. Marking as error.")
        # If AI completely fails, we mark as error so we don't loop forever
        supabase.table("videos").update({"status": "error"}).eq("id", vid_id).execute()
        return

    # D. Generate Image
    print("Generating Image via Hugging Face...")
    img_prompt = f"high quality editorial thumbnail, {title}, 4k, realistic, vivid colors"
    img_binary = generate_image_hf(img_prompt)
    img_url = upload_imgbb(img_binary)
    
    if not img_url:
        print("Warning: Image upload failed. Publishing without featured image.")
        img_url = "" # Continue anyway

    # E. Publish to WordPress
    print("Publishing to WordPress...")
    wp_data = {
        "title": title,
        "content": blog_content,
        "status": "publish",
        "fifu_image_url": img_url, 
        "fifu_image_alt": title
    }
    
    try:
        wp_res = requests.post(WP_URL, json=wp_data, auth=(WP_USER, WP_PASS))

        if wp_res.status_code == 201:
            # F. Update DB to 'published'
            supabase.table("videos").update({"status": "published"}).eq("id", vid_id).execute()
            print("SUCCESS: Published!")
        else:
            print(f"WP Error {wp_res.status_code}: {wp_res.text}")
            
    except Exception as e:
        print(f"WP Connection Error: {e}")

if __name__ == "__main__":
    main()