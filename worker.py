import os
import requests
import re
from supabase import create_client, Client
from youtube_transcript_api import YouTubeTranscriptApi
from huggingface_hub import InferenceClient
import random
import time
import base64

print("DEBUG: Running FINAL CLEANED VERSION (No Markdown + URL Embed)")

# 1. SETUP & CONFIG
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
HF_TOKEN = os.environ["HF_TOKEN"]
IMGBB_KEY = os.environ["IMGBB_KEY"]
WP_USER = os.environ["WP_USER"]
WP_PASS = os.environ["WP_PASS"]
# Base URL for WP API
WP_API_BASE = "https://test.harshtrivedi.in/wp-json/wp/v2"

# Initialize Clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
hf_client = InferenceClient(token=HF_TOKEN)

def clean_markdown(text):
    """
    Forcefully removes Markdown artifacts that the AI might leave behind.
    Converts **Bold** to <b>Bold</b> and ### Headings to <h3>.
    """
    if not text: return ""
    
    # 1. Convert Headings (### Title -> <h3>Title</h3>)
    text = re.sub(r'###\s*(.*?)(?:\n|$)', r'<h3>\1</h3>\n', text)
    text = re.sub(r'##\s*(.*?)(?:\n|$)', r'<h2>\1</h2>\n', text)
    
    # 2. Convert Bold (**Text** -> <b>Text</b>)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    
    # 3. Clean up loose asterisks
    text = text.replace('**', '')
    
    return text

def generate_ai_content(prompt):
    print("DEBUG: Requesting text from Llama 3...")
    messages = [{"role": "user", "content": prompt}]
    try:
        response = hf_client.chat_completion(
            model="meta-llama/Meta-Llama-3-8B-Instruct", 
            messages=messages,
            max_tokens=4000,
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        # Basic cleanup
        content = content.replace('"', '').replace("Here is the blog post:", "")
        return content
    except Exception as e:
        print(f"AI Error: {e}")
        return None

def generate_image_pollinations(prompt):
    print("DEBUG: Requesting image from Pollinations...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    for i in range(3):
        try:
            encoded_prompt = requests.utils.quote(prompt)
            seed = random.randint(1, 100000)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&seed={seed}&nologo=true"
            
            print(f"DEBUG: Fetching {image_url}...")
            response = requests.get(image_url, headers=headers, timeout=45)
            
            if response.status_code == 200 and len(response.content) > 1000:
                print("DEBUG: Image received successfully.")
                return response.content
        except Exception as e:
            time.sleep(2)
    return None

def upload_media_to_wordpress(image_binary, title):
    print("DEBUG: Uploading image to WordPress Media Library...")
    media_url = f"{WP_API_BASE}/media"
    headers = {
        "Content-Type": "image/jpeg",
        "Content-Disposition": f'attachment; filename="{title}.jpg"'
    }
    
    try:
        response = requests.post(
            media_url,
            data=image_binary,
            headers=headers,
            auth=(WP_USER, WP_PASS),
            timeout=60
        )
        
        if response.status_code == 201:
            media_id = response.json()['id']
            print(f"DEBUG: Media uploaded successfully. ID: {media_id}")
            return media_id
        else:
            print(f"WP Media Upload Failed: {response.text}")
            return None
    except Exception as e:
        print(f"WP Media Upload Error: {e}")
        return None

def main():
    print("DEBUG: Initializing...")
    
    # 1. Fetch Video
    response = supabase.table("videos").select("*").eq("status", "pending").limit(1).execute()
    if not response.data:
        print("No pending videos found.")
        return

    video = response.data[0]
    vid_id = video['id']
    raw_title = video['title']
    description = video.get('description', '') or ""
    
    print(f"Processing: {raw_title}")

    # 2. Transcript
    transcript_text = ""
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
        print("Transcript found.")
    except Exception:
        print("Transcript unavailable. Using Description.")
        transcript_text = f"Visual video. Description: {description}"

    context = transcript_text[:4500]

    # 3. Generate Title
    print("Generating Title...")
    title_prompt = f"""
    Write a single, catchy, SEO-friendly blog post title for: "{raw_title}".
    Context: "{context[:500]}"
    Rules: NO hashtags, NO quotes. Just the text.
    """
    new_title = generate_ai_content(title_prompt)
    if not new_title: new_title = raw_title
    
    # CLEAN TITLE (Just in case)
    new_title = clean_markdown(new_title)

    # 4. Generate Body
    print("Generating Blog Body...")
    
    # Custom CTA HTML
    social_links = {
        "YouTube": "https://www.youtube.com/@HAWIStudios",
        "Facebook": "https://www.facebook.com/HAWIStudios",
        "Instagram": "https://www.instagram.com/HAWIStudios",
        "X": "https://x.com/HAWIStudios"
    }
    
    cta_html = f"""
    <div style="background-color: #f9f9f9; padding: 20px; border-radius: 5px; margin-top: 40px; border-left: 5px solid #0073aa;">
    <h3>Support Our Work</h3>
    <p>If you enjoyed this article, please show your support by subscribing to our <a href="{social_links['YouTube']}" target="_blank">YouTube Channel</a>!</p>
    <p>Follow us on: 
    <a href="{social_links['Facebook']}" target="_blank">Facebook</a> | 
    <a href="{social_links['Instagram']}" target="_blank">Instagram</a> | 
    <a href="{social_links['X']}" target="_blank">X (Twitter)</a>
    </p>
    </div>
    """

    body_prompt = f"""
    Write a detailed, Long-Form blog post (minimum 400 words) about: "{new_title}".
    Context: "{context}"
    
    STRUCTURE:
    1. **Opening Quote:** Start with a relevant, unique quote wrapped in <blockquote> tags.
    2. **Introduction:** Engaging hook describing the scene.
    3. **Deep Dive:** 2-3 detailed paragraphs analyzing the behavior, environment, or topic seen in the video.
    4. **Did You Know?:** A section with a surprising fact.
    5. **Conclusion:** A thoughtful wrap-up.
    
    RULES:
    - Write AT LEAST 400 words.
    - NEVER mention "HAWI Studios" or cameramen.
    - Use HTML tags (<h2>, <p>). 
    - Do NOT use Markdown (do not use **, do not use ##).
    """
    
    raw_body = generate_ai_content(body_prompt)
    
    if not raw_body:
        print("Text generation failed.")
        supabase.table("videos").update({"status": "error"}).eq("id", vid_id).execute()
        return

    # CLEAN BODY (Remove stubborn markdown)
    clean_body = clean_markdown(raw_body)

    # 5. Embed Video (The OEmbed Way)
    # WordPress automatically turns a plain URL on its own line into a player.
    # This is safer than iframe tags which get stripped by the API.
    video_url_block = f"\nhttps://www.youtube.com/watch?v={vid_id}\n"
    
    # Combine: Video URL first (so it's at the top), then body, then CTA
    final_content = f"{video_url_block}\n{clean_body}\n{cta_html}"

    # 6. Generate & Upload Image
    print("Generating Image...")
    img_prompt = f"YouTube video thumbnail style for {new_title}, high contrast, vivid colors, text-free, 4k, cinematic lighting, engaging composition"
    img_binary = generate_image_pollinations(img_prompt)
    
    featured_media_id = 0
    if img_binary:
        featured_media_id = upload_media_to_wordpress(img_binary, new_title)
    
    if not featured_media_id:
        print("Warning: Image upload failed. Posting without featured image.")

    # 7. Publish Post
    print(f"Publishing: {new_title}")
    wp_data = {
        "title": new_title,
        "content": final_content,
        "status": "publish",
        "featured_media": featured_media_id
    }
    
    try:
        wp_res = requests.post(f"{WP_API_BASE}/posts", json=wp_data, auth=(WP_USER, WP_PASS), timeout=60)
        
        if wp_res.status_code == 201:
            supabase.table("videos").update({"status": "published"}).eq("id", vid_id).execute()
            print("SUCCESS: Published!")
        else:
            print(f"WP Error {wp_res.status_code}: {wp_res.text}")
    except Exception as e:
        print(f"WP Connection Error: {e}")

if __name__ == "__main__":
    main()