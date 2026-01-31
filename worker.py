import os
import requests
import json
import re
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

def clean_json_response(response_text):
    """
    Cleans AI output to ensure valid JSON.
    """
    cleaned = re.sub(r'```json\s*', '', response_text, flags=re.IGNORECASE)
    cleaned = re.sub(r'```\s*$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def insert_video_embed(html_body, video_id):
    """
    Inserts a YouTube iframe after the first paragraph (<p>...</p>).
    """
    embed_code = f'\n<p><iframe width="560" height="315" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></p>\n'
    
    # Find the end of the first paragraph
    match = re.search(r'</p>', html_body, flags=re.IGNORECASE)
    if match:
        # Insert after the first </p>
        return html_body[:match.end()] + embed_code + html_body[match.end():]
    else:
        # If no paragraph found, prepend to top
        return embed_code + html_body

def generate_blog_content(title, context):
    prompt = f"""
    You are an expert SEO blog writer. Transform this YouTube video into a blog post.
    
    VIDEO INFO:
    Original Title: {title}
    Context: {context[:4000]}
    
    INSTRUCTIONS:
    1. Create a NEW, catchy, SEO-friendly title (do not use the original one). No hashtags.
    2. Write an engaging blog post body using HTML tags (<h2>, <h3>, <p>, <ul>, <li>).
    3. The body must NOT contain the title (H1), as that is handled separately.
    
    OUTPUT FORMAT:
    Return strictly a JSON object with two keys:
    {{
        "seo_title": "Your new catchy title here",
        "html_body": "Your html content here..."
    }}
    """
    
    messages = [{"role": "user", "content": prompt}]
    
    try:
        response = hf_client.chat_completion(
            model="meta-llama/Meta-Llama-3-8B-Instruct", 
            messages=messages,
            max_tokens=2000,
            temperature=0.7
        )
        content = response.choices[0].message.content
        
        try:
            clean_content = clean_json_response(content)
            data = json.loads(clean_content)
            return data
        except json.JSONDecodeError:
            print("JSON Parse Error. Fallback to raw text.")
            return {
                "seo_title": f"Review: {title}",
                "html_body": f"<p>{content}</p>"
            }
            
    except Exception as e:
        print(f"AI Text Gen Error: {e}")
        return None

def generate_image_hf(prompt):
    try:
        # SWITCHED: CompVis/stable-diffusion-v1-4 is the most reliable free model
        image = hf_client.text_to_image(
            prompt=prompt,
            model="CompVis/stable-diffusion-v1-4" 
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
    
    response = supabase.table("videos").select("*").eq("status", "pending").limit(1).execute()
    
    if not response.data:
        print("No pending videos found.")
        return

    video = response.data[0]
    vid_id = video['id']
    original_title = video['title']
    description = video.get('description', '') or ""
    
    print(f"Processing: {original_title}")

    # B. Get Transcript (Safe Mode)
    transcript_text = ""
    try:
        print("Attempting to fetch transcript...")
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        transcript_text = " ".join([t['text'] for t in transcript_list])
        print("Transcript fetched!")
    except Exception as e:
        print(f"Transcript unavailable. Using Description.")
        transcript_text = f"Visual video description: {description}"

    # C. Generate Blog
    print("Generating Blog content...")
    blog_data = generate_blog_content(original_title, transcript_text)
    
    if not blog_data:
        print("Failed to generate text. Marking error.")
        supabase.table("videos").update({"status": "error"}).eq("id", vid_id).execute()
        return

    new_title = blog_data.get("seo_title", original_title)
    html_body = blog_data.get("html_body", "<p>Content generation failed.</p>")

    # --- NEW: Embed Video Logic ---
    print("Embedding Video...")
    html_body = insert_video_embed(html_body, vid_id)
    # ------------------------------

    # D. Generate Image
    print("Generating Image...")
    img_prompt = f"editorial photography, {new_title}, cinematic lighting, 4k, realistic"
    img_binary = generate_image_hf(img_prompt)
    img_url = upload_imgbb(img_binary)
    
    if not img_url:
        print("Warning: Image failed. Publishing text only.")
        img_url = "" 

    # E. Publish to WordPress
    print(f"Publishing: {new_title}")
    wp_data = {
        "title": new_title,
        "content": html_body,
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