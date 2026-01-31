import os
import requests
import json
import re
from supabase import create_client, Client
from youtube_transcript_api import YouTubeTranscriptApi
from huggingface_hub import InferenceClient
import io
import time

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

def strip_hashtags(text):
    """Removes hashtags from titles to clean them up."""
    return re.sub(r'#\w+', '', text).strip()

def smart_parse_json(text):
    """
    Attempts to find and parse a JSON object even if the AI adds extra text.
    """
    try:
        # 1. Try direct parse
        return json.loads(text)
    except:
        pass

    try:
        # 2. Regex search for the first { ... } block
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except:
        pass
    
    return None

def generate_blog_content(title, context):
    prompt = f"""
    You are an expert SEO blog writer.
    
    TASK:
    Write a blog post for the video titled: "{title}"
    Context: "{context[:3000]}"
    
    OUTPUT REQUIREMENTS:
    1. "seo_title": Write a catchy, professional title (NO hashtags).
    2. "html_body": Write the article body in HTML format.
       - Use <h2> for section headers.
       - Use <p> for paragraphs.
       - Do NOT include the title in the html_body (it goes in the title field).
       - Do NOT include <html> or <body> tags.
    
    CRITICAL: Output ONLY valid JSON. No conversational text.
    
    Example format:
    {{
      "seo_title": "The Amazing World of Elephants",
      "html_body": "<p>Elephants are majestic creatures...</p><h2>Habitat</h2><p>They live in...</p>"
    }}
    """
    
    messages = [{"role": "user", "content": prompt}]
    
    try:
        response = hf_client.chat_completion(
            model="meta-llama/Meta-Llama-3-8B-Instruct", 
            messages=messages,
            max_tokens=2500,
            temperature=0.6 # Lower temp for more stability
        )
        raw_content = response.choices[0].message.content
        
        # Smart Parse
        data = smart_parse_json(raw_content)
        
        if data:
            return data
        else:
            # FALLBACK: If JSON completely fails, treat raw text as body
            print("JSON parsing failed completely. Using raw text fallback.")
            clean_body = raw_content.replace('```json', '').replace('```', '').strip()
            return {
                "seo_title": title, # Use cleaned original title
                "html_body": f"<p>{clean_body}</p>"
            }

    except Exception as e:
        print(f"AI Text Gen Error: {e}")
        return None

def generate_image_hf_with_retry(prompt):
    # List of reliable FREE models to try
    models = [
        "CompVis/stable-diffusion-v1-4",
        "runwayml/stable-diffusion-v1-5",
        "stabilityai/stable-diffusion-2-1"
    ]
    
    for model in models:
        try:
            print(f"Trying image model: {model}...")
            image = hf_client.text_to_image(prompt=prompt, model=model)
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG')
            return img_byte_arr.getvalue()
        except Exception as e:
            print(f"Model {model} failed: {e}")
            time.sleep(2) # Wait a bit before retry
            
    print("All image models failed.")
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

def insert_video_embed(html_body, video_id):
    """
    Inserts video. Tries after first paragraph, falls back to top.
    """
    embed_code = f'\n<div class="video-container" style="text-align:center; margin: 20px 0;"><iframe width="100%" height="400" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe></div>\n'
    
    # Try to inject after the first closing </p> tag
    if "</p>" in html_body:
        return html_body.replace("</p>", f"</p>{embed_code}", 1)
    
    # Fallback: Prepend to the top
    return embed_code + html_body

def main():
    print("Connecting to Supabase...")
    response = supabase.table("videos").select("*").eq("status", "pending").limit(1).execute()
    
    if not response.data:
        print("No pending videos found.")
        return

    video = response.data[0]
    vid_id = video['id']
    # Clean the title immediately
    raw_title = video['title']
    clean_title = strip_hashtags(raw_title)
    description = video.get('description', '') or ""
    
    print(f"Processing: {clean_title}")

    # --- 1. TRANSCRIPT ---
    transcript_text = ""
    try:
        print("Fetching transcript...")
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
        print("Transcript found.")
    except Exception as e:
        print("Transcript unavailable. Using Description.")
        transcript_text = f"Visual video. Description: {description}"

    # --- 2. BLOG TEXT ---
    print("Generating Blog...")
    blog_data = generate_blog_content(clean_title, transcript_text)
    
    if not blog_data:
        print("Failed to generate text. Marking error.")
        supabase.table("videos").update({"status": "error"}).eq("id", vid_id).execute()
        return

    final_title = strip_hashtags(blog_data.get("seo_title", clean_title))
    html_body = blog_data.get("html_body", "<p>Content generation error.</p>")

    # --- 3. EMBED VIDEO ---
    print("Embedding Video...")
    final_html = insert_video_embed(html_body, vid_id)

    # --- 4. IMAGE ---
    print("Generating Image...")
    img_url = ""
    img_prompt = f"nature documentary photo, {final_title}, award winning photography, 4k"
    img_binary = generate_image_hf_with_retry(img_prompt)
    
    if img_binary:
        img_url = upload_imgbb(img_binary)
        if not img_url: print("ImgBB Upload Failed.")
    else:
        print("Image generation skipped after retries.")

    # --- 5. PUBLISH ---
    print(f"Publishing: {final_title}")
    wp_data = {
        "title": final_title,
        "content": final_html,
        "status": "publish",
        "fifu_image_url": img_url, 
        "fifu_image_alt": final_title
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