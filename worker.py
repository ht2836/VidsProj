import os
import requests
import re
from youtube_transcript_api import YouTubeTranscriptApi
from huggingface_hub import InferenceClient
import random
import time
from supabase import create_client, Client

print("DEBUG: Running FINAL ROBUST VERSION (Images + Links)")

# ==========================================
# 1. SOCIAL MEDIA CONFIGURATION (EDIT THESE)
# ==========================================
SOCIAL_LINKS = {
    "YouTube": "https://www.youtube.com/@HAWIStudios",
    "Facebook": "https://www.facebook.com/HAWIStudios",
    "Instagram": "https://www.instagram.com/HAWIStudios",
    "X (Twitter)": "https://x.com/HAWIStudios",
    "Bluesky": "https://bsky.app/profile/HAWIStudios.bsky.social"
}

# ==========================================
# 2. SYSTEM CONFIG (DO NOT EDIT)
# ==========================================
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
HF_TOKEN = os.environ["HF_TOKEN"]
IMGBB_KEY = os.environ["IMGBB_KEY"]
WP_USER = os.environ["WP_USER"]
WP_PASS = os.environ["WP_PASS"]
WP_URL = "https://test.harshtrivedi.in/wp-json/wp/v2/posts"

# Initialize Clients
hf_client = InferenceClient(token=HF_TOKEN)

def generate_ai_content(prompt):
    print("DEBUG: Requesting text from Llama 3...")
    messages = [{"role": "user", "content": prompt}]
    try:
        response = hf_client.chat_completion(
            model="meta-llama/Meta-Llama-3-8B-Instruct", 
            messages=messages,
            max_tokens=2500,
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        # Clean up artifacts
        content = content.replace('"', '').replace("Here is the blog post:", "")
        return content
    except Exception as e:
        print(f"AI Error: {e}")
        return None

def generate_image_pollinations(prompt):
    print("DEBUG: Requesting image from Pollinations...")
    
    # Fake browser headers to prevent blocking
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    # Retry loop (3 attempts)
    for i in range(3):
        try:
            encoded_prompt = requests.utils.quote(prompt)
            seed = random.randint(1, 100000)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=576&seed={seed}&nologo=true"
            
            print(f"DEBUG: Attempt {i+1} - Fetching {image_url}...")
            response = requests.get(image_url, headers=headers, timeout=45)
            
            if response.status_code == 200 and len(response.content) > 1000:
                print("DEBUG: Image received successfully.")
                return response.content
            else:
                print(f"DEBUG: Pollinations returned status {response.status_code}")
                
        except Exception as e:
            print(f"DEBUG: Image attempt {i+1} failed: {e}")
            time.sleep(2)
            
    print("CRITICAL: All image generation attempts failed.")
    return None

def upload_imgbb(image_binary):
    if not image_binary: return None
    print("DEBUG: Uploading to ImgBB...")
    payload = {"key": IMGBB_KEY}
    files = {"image": image_binary}
    try:
        res = requests.post("https://api.imgbb.com/1/upload", data=payload, files=files, timeout=60)
        if res.status_code == 200:
            url = res.json()['data']['url']
            print(f"DEBUG: Image hosted at {url}")
            return url
        print(f"ImgBB Failed: {res.text}")
    except Exception as e:
        print(f"ImgBB connection error: {e}")
    return None

def main():
    print("DEBUG: Initializing Supabase...")
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"CRITICAL: Supabase connection failed. {e}")
        return

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

    # --- 1. TRANSCRIPT ---
    transcript_text = ""
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
        print("Transcript found.")
    except Exception:
        print("Transcript unavailable. Using Description.")
        transcript_text = f"Visual video. Description: {description}"

    context = transcript_text[:4000]

    # --- 2. GENERATE TITLE ---
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

    # --- 3. GENERATE BODY (With Hyperlinks) ---
    print("Generating Blog Body...")
    
    # Construct the CTA HTML string dynamically
    cta_html = f"""
    <div style="background-color: #f0f0f0; padding: 20px; border-radius: 10px; margin-top: 30px;">
    <h3>Support Our Work</h3>
    <p>If you enjoyed this, please show your support by subscribing to our <a href="{SOCIAL_LINKS['YouTube']}" target="_blank" rel="noopener">YouTube Channel</a>!</p>
    <p>You can also follow us on: 
    <a href="{SOCIAL_LINKS['Facebook']}" target="_blank">Facebook</a>, 
    <a href="{SOCIAL_LINKS['Instagram']}" target="_blank">Instagram</a>, 
    <a href="{SOCIAL_LINKS['X (Twitter)']}" target="_blank">X (Twitter)</a>, and 
    <a href="{SOCIAL_LINKS['Bluesky']}" target="_blank">Bluesky</a>.
    </p>
    </div>
    """

    body_prompt = f"""
    Write a blog post about: "{new_title}".
    Context: "{context}"
    
    STRICT STRUCTURE:
    1. Start with a relevant, non-repeatable QUOTE about the topic (wrap in <blockquote>).
    2. Write an engaging paragraph describing the video content.
    3. Include a "Did You Know?" section with a random fact about the topic.
    4. Conclude with a final thought.
    
    RESTRICTIONS:
    - NEVER mention "HAWI Studios".
    - NEVER mention the cameraman's name.
    - Do NOT write the Call to Action yourself; I will append it.
    
    FORMATTING:
    - Use HTML tags: <blockquote>, <p>, <h3>.
    - Do NOT use Markdown.
    - Do NOT include the title.
    """
    
    generated_body = generate_ai_content(body_prompt)
    
    if not generated_body:
        print("Text generation failed.")
        supabase.table("videos").update({"status": "error"}).eq("id", vid_id).execute()
        return

    # Combine Body + Custom CTA
    full_html_body = f"{generated_body}\n{cta_html}"

    # --- 4. EMBED VIDEO ---
    video_url = f"https://www.youtube.com/watch?v={vid_id}"
    final_content = f"{video_url}\n\n{full_html_body}"

    # --- 5. GENERATE IMAGE ---
    print("Generating Image...")
    img_prompt = f"cinematic shot, {new_title}, wildlife photography, hyperrealistic, 4k, award winning"
    img_binary = generate_image_pollinations(img_prompt)
    
    img_url = ""
    if img_binary:
        img_url = upload_imgbb(img_binary)
        if not img_url: print("ImgBB Upload Failed.")
    else:
        print("WARNING: Image generation failed (using default).")
        # Optional: Add a fallback image URL here if you have one
        # img_url = "https://your-site.com/default-image.jpg"

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
        print("DEBUG: Sending to WordPress...")
        wp_res = requests.post(WP_URL, json=wp_data, auth=(WP_USER, WP_PASS), timeout=60)
        
        if wp_res.status_code == 201:
            supabase.table("videos").update({"status": "published"}).eq("id", vid_id).execute()
            print("SUCCESS: Published!")
        else:
            print(f"WP Error {wp_res.status_code}: {wp_res.text}")
    except Exception as e:
        print(f"WP Connection Error: {e}")

if __name__ == "__main__":
    main()