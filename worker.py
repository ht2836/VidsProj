import os
import requests
import re
from supabase import create_client, Client
from youtube_transcript_api import YouTubeTranscriptApi
from huggingface_hub import InferenceClient
import random
import time

print("DEBUG: Running FINAL POLLINATIONS VERSION")

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

def generate_ai_content(prompt):
    """
    Generic function to get clean text from Llama 3.
    """
    messages = [{"role": "user", "content": prompt}]
    try:
        response = hf_client.chat_completion(
            model="meta-llama/Meta-Llama-3-8B-Instruct", 
            messages=messages,
            max_tokens=2000,
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        # Clean up common AI artifacts
        content = content.replace('"', '').replace("Here is the blog post:", "")
        return content
    except Exception as e:
        print(f"AI Error: {e}")
        return None

def generate_image_pollinations(prompt):
    """
    Uses Pollinations.ai (Completely Free, No API Key).
    This bypasses Hugging Face's 402/404 errors.
    """
    try:
        # Encode prompt for URL
        encoded_prompt = requests.utils.quote(prompt)
        # Random seed to ensure uniqueness
        seed = random.randint(1, 10000)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=576&seed={seed}&nologo=true"
        
        # Verify it works (Pollinations creates image on the fly)
        response = requests.get(image_url)
        if response.status_code == 200:
            return response.content
        else:
            print(f"Pollinations Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"Image Gen Error: {e}")
        return None

def upload_imgbb(image_binary):
    if not image_binary: return None
    payload = {"key": IMGBB_KEY}
    files = {"image": image_binary}
    try:
        res = requests.post("https://api.imgbb.com/1/upload", data=payload, files=files)
        if res.status_code == 200:
            return res.json()['data']['url']
    except:
        pass
    return None

def main():
    print("Connecting to Supabase...")
    
    # Fetch 1 pending video
    response = supabase.table("videos").select("*").eq("status", "pending").limit(1).execute()
    
    if not response.data:
        print("No pending videos found.")
        return

    video = response.data[0]
    vid_id = video['id']
    raw_title = video['title']
    description = video.get('description', '') or ""
    
    print(f"Processing: {raw_title}")

    # --- 1. TRANSCRIPT (Safe Fetch) ---
    transcript_text = ""
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
        print("Transcript found.")
    except Exception:
        print("Transcript unavailable. Using Description.")
        transcript_text = f"Visual video. Description: {description}"

    context = transcript_text[:4000]

    # --- 2. GENERATE TITLE (Separate Call) ---
    print("Generating Title...")
    title_prompt = f"""
    Write a single, catchy, SEO-friendly blog post title for a video about: "{raw_title}".
    Context: "{context[:500]}"
    Rules:
    - Do NOT use hashtags.
    - Do NOT use quotes.
    - Output ONLY the title text.
    """
    new_title = generate_ai_content(title_prompt)
    if not new_title: new_title = raw_title

    # --- 3. GENERATE BODY (Strict Structure) ---
    print("Generating Blog Body...")
    body_prompt = f"""
    Write a blog post about: "{new_title}".
    Context: "{context}"
    
    STRICT STRUCTURE INSTRUCTIONS:
    1. Start with a relevant, non-repeatable QUOTE about the topic (in italics <blockquote>).
    2. Then write an engaging paragraph describing the video content.
    3. Then include a "Did You Know?" section with a random fact about the topic.
    4. Conclude with a final paragraph.
    5. END EXACTLY WITH THIS CALL TO ACTION: "If you want to support us, show the support by subscribing to our channel on YouTube, and follow us on FB, Insta, X, and Bluesky."
    
    RESTRICTIONS:
    - NEVER mention "HAWI Studios".
    - NEVER mention the cameraman's name.
    
    FORMATTING:
    - Use HTML tags: <blockquote>, <p>, <h3>.
    - Do NOT use Markdown.
    - Do NOT include the title in the body.
    """
    html_body = generate_ai_content(body_prompt)
    
    if not html_body:
        print("Text generation failed. Skipping.")
        supabase.table("videos").update({"status": "error"}).eq("id", vid_id).execute()
        return

    # --- 4. EMBED VIDEO (The WordPress "Magic" Way) ---
    # Placing the URL on the very first line forces WordPress to use oEmbed
    video_url = f"https://www.youtube.com/watch?v={vid_id}"
    final_content = f"{video_url}\n\n{html_body}"

    # --- 5. GENERATE IMAGE (Pollinations) ---
    print("Generating Image (Pollinations)...")
    img_prompt = f"cinematic shot, {new_title}, wildlife photography, hyperrealistic, 4k, award winning"
    img_binary = generate_image_pollinations(img_prompt)
    
    img_url = ""
    if img_binary:
        img_url = upload_imgbb(img_binary)
        if not img_url: print("ImgBB Upload Failed.")
    else:
        print("Image generation failed.")

    # --- 6. PUBLISH ---
    print(f"Publishing: {new_title}")
    wp_data = {
        "title": new_title,
        "content": final_content,
        "status": "publish",
        "fifu_image_url": img_url, 
        "fifu_image_alt": new_title
    }
    
    try:
        wp_res = requests.post(WP_URL, json=wp_data, auth=(WP_USER, WP_PASS))
        
        if wp_res.status_code == 201:
            supabase.table("videos").update({"status": "published"}).eq("id", vid_id).execute()
            print("SUCCESS: Published!")
        else:
            print(f"WP Error {wp_res.status_code}: {wp_res.text}")
    except Exception as e:
        print(f"WP Connection Error: {e}")

if __name__ == "__main__":
    main()