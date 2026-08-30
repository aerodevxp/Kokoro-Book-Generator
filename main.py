import streamlit as st
import openai
import os
from pathlib import Path
import time
import json
import re
import random
import shutil
import threading
import subprocess
import gc
from fpdf import FPDF
from mutagen.id3 import ID3, APIC, TIT2, TPE1
from dotenv import load_dotenv
from pydub import AudioSegment
from pydub.effects import strip_silence

import requests

# Load environment variables
load_dotenv()

# Configuration
STORY_MODEL = os.getenv("STORY_MODEL")
TITLE_MODEL = os.getenv("TITLE_MODEL")
IMG_MODEL = os.getenv("IMG_MODEL")

BASE_URL = os.getenv("BASE_URL")
TTS_URL = os.getenv("TTS_URL")
IMG_URL = os.getenv("IMG_URL")

FREESOUND_API_KEY = os.getenv("FREESOUND_API_KEY", "")

BASE_PROMPT_PATH = os.getenv("BASE_PROMPT_PATH")
OUTPUT_DIR = os.getenv("OUTPUT_DIR")
WORLDBOOK_DIR = os.getenv("WORLDBOOK_DIR", os.path.join(OUTPUT_DIR, "worldbooks"))
SERIES_DIR = os.getenv("SERIES_DIR", os.path.join(OUTPUT_DIR, "series"))
FEATURES_FILE = os.getenv("FEATURES_FILE", os.path.join(OUTPUT_DIR, "features.txt"))
JOBS_DIR = os.getenv("JOBS_DIR", os.path.join(OUTPUT_DIR, "jobs"))
SFX_DIR = os.getenv("SFX_DIR", os.path.join(OUTPUT_DIR, "sfx"))

VALID_VOICES = {
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro",
    "jm_kumo",
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
    "ef_dora", "em_alex", "em_santa",
    "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    "if_sara", "im_nicola",
    "pf_dora", "pm_alex", "pm_santa",
}

VOICE_PATTERN = re.compile(r"<(am|af|bm|bf|ef|em|ff|hf|hm|if|im|jf|jm|pf|pm|zf|zm)[^>]*>([^<]*)</\1[^>]*>")
PAUSE_PATTERN = re.compile(r'\x5Bpause:\d+\.?\d*s\x5D', re.IGNORECASE)
RATE_PATTERN = re.compile(r'\x5Brate:\d+\.?\d*\x5D')
IPA_PATTERN = re.compile(r'\x5B([^\x5D]+)\x5D\x28/([^\x5D]+)/\x29')
SFX_CACHE_FILE = os.path.join(SFX_DIR, "sfx_cache.json")
CUSTOM_SFX_DIR = os.path.join(SFX_DIR, "custom")

TEST_STORY = """[bgsfx:rain] The alley was cold and wet. Rain hammered down on the dumpsters, echoing off the brick walls.

<af_heart>Raph pulled his jacket tighter, watching the two figures approach through the downpour.</af_heart>

<am_adam>"You're late," [rate:0.9] Adam growled, stepping under the flickering streetlight. "And you brought the money?"</am_adam>

<af_bella>"He's lying," [pause:0.5s] Bella snapped, stepping out from behind Adam. "I saw him pocket half of it yesterday."</af_bella>

<af_heart>Raph didn't flinch. He knew better than to show fear.</af_heart>

<am_adam>"Is that true, Raph?" [pause:1s] Adam stepped closer, his fists clenching.</am_adam>

<af_heart>Before Raph could answer, Adam swung. [sfx:punch] The impact sent Raph stumbling back into the wet bricks.</af_heart>

<af_bella>"Get up!" [rate:1.1] Bella yelled. "Don't let him disrespect you like that!"</af_bella>

<af_heart>Raph wiped the blood from his lip. He pushed off the wall and lunged. [sfx:slap] The sharp sound echoed through the alley, silencing the rain for a brief second.</af_heart>

<am_adam>Adam held his cheek, his eyes wide in shock. "You... you actually hit me."</am_adam>

<af_heart>The rain continued to pour. [pause:1.5s] Raph stood his ground, waiting for the next move.</af_heart>

[/bgsfx]"""

@st.cache_resource
def get_clients():
    llm = openai.OpenAI(base_url=BASE_URL, api_key=os.getenv("LLM_API_KEY", "dummy-key"), timeout=1800.0)
    tts = openai.OpenAI(base_url=TTS_URL, api_key=os.getenv("TTS_API_KEY", "not-needed"), timeout=900.0)
    return llm, tts

llm_client, tts_client = get_clients()

def read_base_prompt():
    try:
        with open(BASE_PROMPT_PATH, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def ensure_directories():
    dirs = [OUTPUT_DIR, WORLDBOOK_DIR, SERIES_DIR, JOBS_DIR, SFX_DIR, CUSTOM_SFX_DIR]
    for directory in dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)

def clean_text_for_tts(text):
    text = text.replace('"', '').replace('"', '').replace('"', '')
    text = text.replace(''', '').replace(''', '')
    text = text.replace('—', ', ').replace('–', ', ')
    text = ' '.join(text.split())
    return text.strip()

def extract_character_voices(worldbook_content):
    voices = {}
    match = re.search(r'\x5BCHARACTER VOICES\x5D(.*?)(?:\n\x5B|\Z)', worldbook_content, re.DOTALL)

    if match:
        voice_section = match.group(1).strip()
        for line in voice_section.split('\n'):
            if ':' in line:
                parts = line.split(':', 1)
                char_name = parts[0].strip()
                voice = parts[1].strip()
                if voice in VALID_VOICES:
                    voices[char_name] = voice
    return voices

def embed_cover_in_mp3(mp3_path, cover_path, title):
    """Embed cover art and metadata into the MP3"""
    try:
        audio = ID3(mp3_path)
    except:
        audio = ID3()
    
    with open(cover_path, 'rb') as f:
        audio.add(APIC(
            encoding=3,
            mime='image/png',
            type=3,
            desc='Cover',
            data=f.read()
        ))
    
    audio.add(TIT2(encoding=3, text=title))
    audio.add(TPE1(encoding=3, text=STORY_MODEL))
    audio.save(mp3_path)

def build_voice_instruction(character_voices=None, available_sfx=None):
    sfx_list = available_sfx if available_sfx else []
    sfx_str = ", ".join(sfx_list) if sfx_list else "None available"
    
    instruction = f"""
VOICE TAG INSTRUCTIONS:
- Wrap ALL character dialogue in voice tags using this format: <voice_name>dialogue</voice_name>
- Wrap ALL narration in voice tags too, using the appropriate narration voice (see NARRATION VOICE below)
- Assign each character a consistent voice from the available Kokoro voices listed below
- Keep character voices consistent throughout the entire story
- The voice name in the tag must be an EXACT match from the available voices list
- Voice tags must come in pair, and have the SAME name!!! Example: <af_heart>Hi!</af_heart> - BAD EXAMPLE: <af_heart>No!!</af_nicole>
- If continuing from a reference story, use the SAME voices for the SAME characters
- The prefix for voice stand for their language and gender. Take that into account when picking a voice.
- Your tags have to be within the same paragraph. You must open and close a tag within the same paragraph.

Prefix Information:

af_ American English – Female
am_ American English – Male
bf_ British English – Female
bm_ British English – Male
jf_ Japanese – Female
jm_ Japanese – Male
zf_ Mandarin Chinese – Female
zm_ Mandarin Chinese – Male
ef_ Spanish – Female
em_ Spanish – Male
ff_ French – Female
hf_ Hindi – Female
hm_ Hindi – Male
if_ Italian – Female
im_ Italian – Male
pf_ Brazilian Portuguese – Female
pm_ Brazilian Portuguese – Male

SOUND EFFECTS (CRITICAL - YOU MUST USE THESE FOR IMMERSION):
- Use [sfx:effect_name] to insert a sound effect that interrupts speech.
- Use [bgsfx:effect_name] to start a background sound effect, and [/bgsfx] to stop it (the closing tag cannot contain the effect name. only one bg can be played at a time, so choose wisely).
- Background sounds continue across multiple paragraphs/voice clips until stopped.
- You can use ANY descriptive effect name. If it's not cached locally, it will be fetched automatically.
- Use descriptive names: door_creak, glass_shatter, wolf_howl, sword_clash, rain_heavy, crowd_market
- Place SFX tags INSIDE voice tags where the sound should occur.
- Available effects already cached: {sfx_str}

Example (Interrupting SFX):
<af_heart>The door opened slowly. [sfx:door_creak] Sarah walked in.</af_heart>

Example (Background SFX):
[bgsfx:rain_heavy] <af_heart>The storm raged outside.</af_heart> <am_adam>We should go inside.</am_adam> [/bgsfx]

VOICE MIXING:
- You can mix voices using weighted ratios: <af_bella(2)+af_heart(1)>mixed voice dialogue</af_bella(2)+af_heart(1)>
- Ratios are normalized automatically (2:1 = 67%/33%)
- Useful for creating unique character voices that don't match any single voice

CONTROL TOKENS (for TTS only — these will be removed from the text/PDF version):
- Pauses: [pause:1.5s] inserts 1.5 seconds of silence. Use for dramatic effect or scene transitions.
- Speech rate: [rate:1.5] speeds up speech by 1.5x until next voice change. [rate:0.7] slows it down. [rate:1.0] resets to normal.
- Pronunciation: [Worcester](/wˈʊstər/) speaks the IPA instead of the word. English only. You can use this to make a character say the same word but in a different way.
- These tokens go INSIDE the voice tags, mixed with the dialogue/narration text.

NARRATION VOICE:
- For omniscient/third-person objective narration: use af_heart
- For first-person POV: use the POV character's voice for narration AND internal thoughts
- For limited third-person POV: use the focal character's voice for narration
- If the story switches POVs between scenes, use whichever character's perspective the current scene is from
- You decide the best narration style based on the story content
"""
    
    if character_voices:
        instruction += "\nCHARACTER VOICES (from worldbook — use these EXACT voices for these characters):\n"
        for char, voice in character_voices.items():
            instruction += f"- {char}: {voice}\n"
        instruction += "\nFor new characters not listed above, assign voices from the available list.\n"
    
    instruction += f"""
Available voices:
Female: af_heart, af_alloy, af_aoede, af_bella, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky, bf_alice, bf_emma, bf_isabella, bf_lily, jf_alpha, jf_gongitsune, jf_nezumi, jf_tebukuro, zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, ef_dora, ff_siwis, hf_alpha, hf_beta, if_sara, pf_dora
Male: am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx, am_puck, am_santa, bm_daniel, bm_fable, bm_george, bm_lewis, jm_kumo, zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang, em_alex, em_santa, hm_omega, hm_psi, im_nicola, pm_alex, pm_santa

Example (omniscient narration with control tokens):
<af_heart>The sun set over the mountains. [pause:0.5s] Long shadows stretched across the valley.</af_heart>
<af_bella>"I can't believe you did that!" [pause:1s] She shook her head in disbelief.</af_bella>
<am_adam>"It was the only way." [rate:0.8] His voice was barely a whisper.</am_adam>

Example (voice mixing for a unique character):
<af_bella(2)+af_nova(1)>"I am something entirely new."</af_bella(2)+af_nova(1)>

Example (pronunciation correction):
<am_michael>He lived in [Worcester](/wˈʊstər/) for years.</am_michael>
"""
    return instruction

def extract_voices_used(story):
    voices = set()
    for match in VOICE_PATTERN.finditer(story):
        voice_tag = match.group(0)
        voice = voice_tag[1:voice_tag.find('>')]
        voices.add(voice)
    return list(voices)

def load_features():
    features = []
    try:
        with open(FEATURES_FILE, 'r') as f:
            features = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        default_features = [
            "Magic System", "Political Intrigue", "Romantic Subplot",
            "Mystery Element", "Action Sequences", "Character Development",
            "Philosophical Themes", "Supernatural Elements",
            "Technology Integration", "Survival Elements"
        ]
        with open(FEATURES_FILE, 'w') as f:
            f.write('\n'.join(default_features))
        features = default_features
    return features

def get_sfx_path(sfx_name):
    sfx_dir = Path(SFX_DIR)
    custom_dir = Path(CUSTOM_SFX_DIR)
    custom_path = custom_dir / f"{sfx_name}.mp3"
    
    if custom_path.exists():
        return custom_path
        
    base_path = sfx_dir / f"{sfx_name}.mp3"
    variants = []
    if base_path.exists():
        variants.append(base_path)
    variants.extend(sfx_dir.glob(f"{sfx_name}_*.mp3"))
    
    fetch_new = False
    if FREESOUND_API_KEY:
        if not variants:
            fetch_new = True
        elif len(variants) < 3:
            fetch_new = random.random() < 0.3
    
    if fetch_new:
        new_path = fetch_new_sfx_variant(sfx_name, variants)
        if new_path:
            return new_path
    
    if variants:
        return random.choice(variants)
    
    print(f"[WARN] SFX '{sfx_name}' not found in custom or fetched folders, and could not be fetched")
    return None

def fetch_new_sfx_variant(sfx_name, existing_variants):
    sfx_dir = Path(SFX_DIR)
    cache = load_sfx_cache()
    downloaded_ids = set(cache.get(sfx_name, []))
    
    try:
        print(f"[INFO] Fetching new SFX variant for '{sfx_name}' from Freesound...")
        search_response = requests.get(
            "https://freesound.org/apiv2/search/text/",
            params={
                "query": sfx_name.replace("_", " "),
                "filter": "license:\"Creative Commons 0\"",
                "sort": "rating_desc",
                "fields": "id,name,duration,previews",
                "page_size": 15
            },
            headers={"Authorization": f"Token {FREESOUND_API_KEY}"},
            timeout=15
        )
        
        if search_response.status_code != 200:
            print(f"[WARN] Freesound search failed: {search_response.status_code}")
            return None
        
        results = search_response.json().get("results", [])
        if not results:
            print(f"[WARN] No Freesound results for '{sfx_name}'")
            return None
        
        candidates = []
        for result in results:
            if result["id"] in downloaded_ids:
                continue
            if result.get("duration", 999) < 10:
                candidates.insert(0, result)
            else:
                candidates.append(result)
        
        if not candidates:
            print(f"[INFO] All Freesound results for '{sfx_name}' already downloaded")
            return None
        
        pick = random.choice(candidates[:3])
        preview_url = pick["previews"].get("preview-hq-mp3")
        if not preview_url:
            preview_url = pick["previews"].get("preview-lq-mp3")
        
        if not preview_url:
            print(f"[WARN] No preview URL for Freesound result")
            return None
        
        audio_response = requests.get(preview_url, timeout=90)
        if audio_response.status_code != 200:
            print(f"[WARN] Freesound download failed: {audio_response.status_code}")
            return None
        
        variant_num = len(existing_variants) + 1
        if variant_num == 1:
            variant_path = sfx_dir / f"{sfx_name}.mp3"
        else:
            variant_path = sfx_dir / f"{sfx_name}_{variant_num}.mp3"
        
        temp_path = sfx_dir / f"{sfx_name}_temp.mp3"
        with open(temp_path, 'wb') as f:
            f.write(audio_response.content)
        
        if not validate_mp3(str(temp_path)):
            print(f"[WARN] Downloaded SFX for '{sfx_name}' was corrupt or invalid. Discarding.")
            temp_path.unlink()
            return None
        
        audio = AudioSegment.from_mp3(str(temp_path))
        audio = strip_silence(audio, silence_thresh=-40, padding=300)
        
        target_dbfs = -20
        change = target_dbfs - audio.dBFS
        audio = audio.apply_gain(change)
        
        audio.export(str(variant_path), format="mp3")
        temp_path.unlink()
        
        if sfx_name not in cache:
            cache[sfx_name] = []
        cache[sfx_name].append(pick["id"])
        save_sfx_cache(cache)
        
        print(f"[INFO] Cached new SFX variant: {variant_path} (Freesound ID: {pick['id']})")
        return variant_path
        
    except Exception as e:
        print(f"[ERROR] Freesound fetch failed for '{sfx_name}': {e}")
        temp_path = sfx_dir / f"{sfx_name}_temp.mp3"
        if temp_path.exists():
            try: temp_path.unlink()
            except: pass
        return None

def get_available_sfx():
    sfx_names = set()
    sfx_dir = Path(SFX_DIR)
    if sfx_dir.exists():
        for f in sfx_dir.glob("*.mp3"):
            if "_temp" in f.name:
                continue
            sfx_names.add(f.stem)
    
    custom_dir = Path(CUSTOM_SFX_DIR)
    if custom_dir.exists():
        for f in custom_dir.glob("*.mp3"):
            sfx_names.add(f.stem)
    
    return sorted(list(sfx_names))

def load_sfx_cache():
    try:
        with open(SFX_CACHE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_sfx_cache(cache):
    with open(SFX_CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

def string_to_pdf(string, outputFullPath):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=72)
    pdf.add_font("DejaVu", "", "./assets/DejaVuSans.ttf", uni=True)
    pdf.set_font("DejaVu", size=12)
    pdf.set_text_color(34, 34, 34)
    
    paragraphs = [p.strip() for p in string.split('\n\n') if p.strip()]
    if not paragraphs:
        paragraphs = [string]
    
    for i, para in enumerate(paragraphs):
        pdf.multi_cell(0, 10, para)
        if i < len(paragraphs) - 1:
            pdf.ln(6)
    
    pdf.output(outputFullPath)
 
def generate_chapter_summary(chapter_text, chapter_num, job_id=None):
    clean_text = remove_voice_tags(chapter_text)
    
    prompt = f"""Summarize this chapter in 350 words max. Include:
- Key events that occurred
- Character developments or revelations
- Important dialogue or decisions
- Any new information or plot threads introduced

Chapter {chapter_num}:
{clean_text}

Chapter summary (150 words max):"""
    
    response = llm_client.chat.completions.create(
        model=STORY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=250,
        temperature=0.3
    )
    
    return response.choices[0].message.content.strip()

def build_running_summary(chapter_summaries):
    if not chapter_summaries:
        return ""
    
    combined = "\n\n".join([f"Chapter {i+1}: {summary}" for i, summary in enumerate(chapter_summaries)])
    return f"STORY SO FAR (summaries of previous chapters):\n{combined}\n\n"

def get_all_stories():
    story_files = []
    for story_dir in Path(OUTPUT_DIR).iterdir():
        if story_dir.is_dir() and story_dir.name not in ["worldbooks", "series", "jobs"]:
            story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            for story_dir in series_dir.iterdir():
                if story_dir.is_dir():
                    story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    return story_files

def generate_story_summary(story_path, job_id=None):
    chapter_summaries_path = story_path.parent / f"{story_path.stem}_chapter_summaries.json"
    if chapter_summaries_path.exists():
        if job_id:
            update_job_status(job_id, "running", 0.1, "Found chapter summaries, combining them...")
        
        with open(chapter_summaries_path, 'r') as f:
            chapter_summaries = json.load(f)
        
        if chapter_summaries:
            summary = generate_book_summary_from_chapters(chapter_summaries, story_path.stem, story_path.parent, job_id)
            return summary
    
    if job_id:
        update_job_status(job_id, "running", 0.05, "No chapter summaries found, chunking full text...")
    
    tts_path = story_path.parent / f"{story_path.stem}_tts.txt"
    if tts_path.exists():
        with open(tts_path, 'r') as f:
            story_content = f.read()
    else:
        with open(story_path, 'r') as f:
            story_content = f.read()
    
    clean_content = remove_voice_tags(story_content)
    
    words = clean_content.split()
    chunk_size = 3000
    chunks = []
    
    for i in range(0, len(words), chunk_size):
        chunk = ' '.join(words[i:i+chunk_size])
        chunks.append(chunk)
    
    if job_id:
        update_job_status(job_id, "running", 0.05, f"Summarizing '{story_path.stem}' in {len(chunks)} chunks...")
    
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        prompt = f"""Summarize this section of a story in 150 words max. Include:
- Key characters and events in this section
- Important plot developments
- Any new settings or relationships

Story section {i+1} of {len(chunks)}:
{chunk}

Section summary (150 words max):"""
        
        response = llm_client.chat.completions.create(
            model=STORY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.3
        )
        
        chunk_summaries.append(response.choices[0].message.content.strip())
        
        if job_id:
            progress = 0.05 + (i + 1) / len(chunks) * 0.05
            update_job_status(job_id, "running", progress, 
                            f"Summarizing '{story_path.stem}': chunk {i+1}/{len(chunks)} done")
    
    combined_text = "\n\n".join(chunk_summaries)
    
    final_prompt = f"""Below are section summaries from a story. Combine them into ONE cohesive summary of maximum 600 words. Include:
- Main characters and their relationships
- Key plot points and events
- Important settings/locations
- How the story ends
- Any unresolved threads or cliffhangers

Section summaries:
{combined_text}

Final summary (600 words max):"""
    
    response = llm_client.chat.completions.create(
        model=STORY_MODEL,
        messages=[{"role": "user", "content": final_prompt}],
        max_tokens=800,
        temperature=0.3
    )
    
    summary = response.choices[0].message.content.strip()
    
    summary_path = story_path.parent / f"{story_path.stem}_summary.txt"
    with open(summary_path, 'w') as f:
        f.write(summary)
    
    return summary

def load_story_context(story_path, job_id=None):
    if not story_path:
        return ""
    try:
        summary_path = story_path.parent / f"{story_path.stem}_summary.txt"
        
        if summary_path.exists():
            with open(summary_path, 'r') as f:
                summary = f.read()
        else:
            print(f"[INFO] No summary found for '{story_path.stem}', generating one...")
            summary = generate_story_summary(story_path, job_id)
        
        return f"Reference Story Summary (from '{story_path.stem}'):\n{summary}\n\n"
    except Exception as e:
        print(f"Error loading/generating story summary: {e}")
        return ""

def get_worldbooks():
    return list(Path(WORLDBOOK_DIR).glob("*.txt"))

def load_worldbook_context(worldbook_path):
    if not worldbook_path:
        return ""
    try:
        with open(worldbook_path, 'r') as f:
            content = f.read()
            return f"World Context (from '{worldbook_path.stem}'):\n{content}\n\n"
    except:
        return ""

def sanitize_title(title):
    safe_title = re.sub(r'[<>:"/|?\*\x00-\x1F.]', '_', title)[:100]
    safe_title = safe_title.rstrip('_')
    return safe_title or "Untitled-Story"

def remove_voice_tags(text):
    """Remove voice tags AND control tokens from text for TXT/PDF output"""
    # Remove voice tags (keep content)
    clean_text = re.sub(r'<([^>]+)>([^<]*)</\1>', r'\2', text)
    # Remove pause tokens entirely
    clean_text = PAUSE_PATTERN.sub('', clean_text)
    # Remove rate tokens entirely  
    clean_text = RATE_PATTERN.sub('', clean_text)
    # Replace IPA pronunciation with just the word
    clean_text = IPA_PATTERN.sub(r'\1', clean_text)
    
    # FIXED: Remove SFX and BGSFX tags entirely (handles spaces, numbers, punctuation)
    clean_text = re.sub(r'\x5Bsfx:[^\x5D]+\x5D', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'\x5B/?bgsfx(?::[^\x5D]*)?\x5D', '', clean_text, flags=re.IGNORECASE)

    # Clean up any double spaces left behind by removed tags
    clean_text = re.sub(r'  +', ' ', clean_text)
    return clean_text

def save_metadata(title, story_type, reference_story, worldbook_used, features_used, story_dir, voices_used=None):
    metadata = {
        "title": title,
        "story_type": story_type,
        "reference_story": str(reference_story) if reference_story else None,
        "worldbook": str(worldbook_used) if worldbook_used else None,
        "features": features_used,
        "voices_used": voices_used or [],
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(story_dir)
    }
    metadata_file = story_dir / f"{sanitize_title(title)}_metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)

def save_story(story, title, series_name=None):
    safe_title = sanitize_title(title)
    if series_name:
        base_dir = Path(SERIES_DIR) / series_name
    else:
        base_dir = Path(OUTPUT_DIR)
    
    story_dir = base_dir / safe_title
    story_dir.mkdir(parents=True, exist_ok=True)
    
    clean_story = remove_voice_tags(story)
    filepath = story_dir / f"{safe_title}.txt"
    with open(filepath, 'w') as f:
        f.write(clean_story)
    
    string_to_pdf(clean_story, str(story_dir / f"{safe_title}.pdf"))
    return filepath, story_dir

def save_tts_story(story, title, story_dir):
    safe_title = sanitize_title(title)
    tts_filepath = story_dir / f"{safe_title}_tts.txt"
    with open(tts_filepath, 'w') as f:
        f.write(story)
    return tts_filepath

def split_into_paragraphs(text):
    return [p.strip() for p in text.split('\n\n') if p.strip()]

def parse_tts_text(text):
    segments = []
    last_end = 0
    matches = list(VOICE_PATTERN.finditer(text))
    for match in matches:
        if match.start() > last_end:
            default_text = text[last_end:match.start()].strip()
            if default_text:
                segments.append({'voice': 'af_heart', 'text': default_text})
        voice_tag = match.group(0)
        voice = voice_tag[1:voice_tag.find('>')]
        content = match.group(2)
        segments.append({'voice': voice, 'text': content.strip()})
        last_end = match.end()
    if last_end < len(text):
        remaining_text = text[last_end:].strip()
        if remaining_text:
            segments.append({'voice': 'af_heart', 'text': remaining_text})
    return segments

def is_valid_voice(voice):
    if voice in VALID_VOICES:
        return True
    if '+' in voice:
        for part in voice.split('+'):
            v = part.split('(')[0].strip()
            if v not in VALID_VOICES:
                return False
        return True
    return False

def extract_mixed_voices(text):
    aliases = {}
    counter = 1
    pattern = re.compile(r'<([^>]+)>([^<]*)</\1>')
    for match in pattern.finditer(text):
        voice = match.group(1)
        if '+' in voice and voice not in aliases.values():
            alias = f"mixed_{counter}"
            aliases[alias] = voice
            counter += 1
    return aliases

def preprocess_sfx_in_voice_tags(text):
    pattern = re.compile(r'<([^>]+)>([^<]*?)(\x5Bsfx:[a-z0-9_]+\x5D|\x5Bbgsfx:[a-z0-9_]+\x5D|\x5B/bgsfx\x5D)([^<]*?)</\1>', re.IGNORECASE)
    while True:
        new_text = pattern.sub(r'<\1>\2</\1> \3 <\1>\4</\1>', text)
        if new_text == text:
            break
        text = new_text
    return text

def fix_sfx_tags(text):
    """Normalizes SFX tags (fixes spaces, strips punctuation) and auto-closes unclosed BGSFX tags"""
    # Normalize tags: [sfx: rain.] -> [sfx:rain], [sfx:footsteps_2] -> [sfx:footsteps_2]
    def clean_sfx(match):
        tag_type = match.group(1)
        name = match.group(2)
        # Remove any punctuation (periods, commas, etc.) and replace spaces with underscores
        name = re.sub(r'[^a-zA-Z0-9_ ]', '', name)
        name = name.replace(' ', '_')
        return f"[{tag_type}:{name}]"
    
    text = re.sub(r'\x5B(sfx|bgsfx):\s*([a-zA-Z0-9_ ]+)\s*\x5D', clean_sfx, text, flags=re.IGNORECASE)

    
    # Auto-close unclosed bgsfx tags
    fixed_text = ""
    open_bgsfx = False
    pattern = re.compile(r'(\x5Bbgsfx:[a-z0-9_]+\x5D|\x5B/bgsfx\x5D)', re.IGNORECASE)
    last_end = 0
    
    for match in pattern.finditer(text):
        fixed_text += text[last_end:match.start()]
        tag = match.group(0)
        
        if tag.lower().startswith('[bgsfx:'):
            if open_bgsfx:
                # Auto close previous bgsfx before opening new one
                fixed_text += '[/bgsfx] '
            fixed_text += tag
            open_bgsfx = True
        elif tag.lower() == '[/bgsfx]':
            if open_bgsfx:
                fixed_text += tag
                open_bgsfx = False
        last_end = match.end()
        
    fixed_text += text[last_end:]
    
    if open_bgsfx:
        fixed_text += ' [/bgsfx]'
        
    return fixed_text

def convert_to_kokoro_format(text, voice_aliases=None):
    if voice_aliases is None:
        voice_aliases = {}
    
    voice_to_alias = {v: k for k, v in voice_aliases.items()}
    pattern = re.compile(r'<([^>]+)>([^<]*)</\1>')
    
    def replace_tag(match):
        voice = match.group(1)
        content = match.group(2)
        
        if not is_valid_voice(voice):
            return match.group(0)
        
        if '+' in voice:
            alias = voice_to_alias.get(voice)
            if alias:
                return f"[voice:{alias}]{content}"
        
        return f"[voice:{voice}]{content}"
    
    return pattern.sub(replace_tag, text)

def get_all_series():
    series_list = []
    if not Path(SERIES_DIR).exists():
        return series_list
    
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            meta_path = series_dir / "series.json"
            if meta_path.exists():
                with open(meta_path, 'r') as f:
                    meta = json.load(f)
                series_list.append(meta)
            else:
                stories = []
                for story_dir in series_dir.iterdir():
                    if story_dir.is_dir():
                        story_files = [f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")]
                        if story_files:
                            stories.append({
                                "title": story_dir.name,
                                "order": len(stories) + 1,
                                "type": "standalone",
                                "reference": None,
                                "path": str(story_files[0].relative_to(series_dir)),
                                "created": time.strftime("%Y-%m-%d %H:%M:%S")
                            })
                meta = {
                    "name": series_dir.name,
                    "worldbook": None,
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "stories": stories
                }
                with open(meta_path, 'w') as f:
                    json.dump(meta, f, indent=2)
                series_list.append(meta)
    
    return series_list

def load_series_metadata(series_name):
    meta_path = Path(SERIES_DIR) / series_name / "series.json"
    if meta_path.exists():
        with open(meta_path, 'r') as f:
            return json.load(f)
    return None

def save_series_metadata(series_name, metadata):
    series_dir = Path(SERIES_DIR) / series_name
    series_dir.mkdir(parents=True, exist_ok=True)
    meta_path = series_dir / "series.json"
    with open(meta_path, 'w') as f:
        json.dump(metadata, f, indent=2)

def add_story_to_series(series_name, title, story_type, reference, story_filepath, worldbook=None):
    if not series_name:
        return
    
    meta = load_series_metadata(series_name)
    if not meta:
        meta = {
            "name": series_name,
            "worldbook": worldbook,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stories": []
        }
    
    if worldbook and not meta.get("worldbook"):
        meta["worldbook"] = worldbook
    
    existing = next((s for s in meta["stories"] if s["title"] == title), None)
    if not existing:
        order = len(meta["stories"]) + 1
        meta["stories"].append({
            "title": title,
            "order": order,
            "type": story_type,
            "reference": str(reference) if reference else None,
            "path": str(story_filepath.relative_to(Path(SERIES_DIR) / series_name)),
            "created": time.strftime("%Y-%m-%d %H:%M:%S")
        })
    
    save_series_metadata(series_name, meta)

def add_existing_story_to_series(story_path, series_name, story_type="standalone", reference=None):
    if not story_path or not series_name:
        return False, "Story path and series name required"
    
    story_path = Path(story_path)
    story_folder = story_path.parent
    story_title = story_folder.name
    
    series_dir = Path(SERIES_DIR) / series_name
    target_folder = series_dir / story_title
    
    if target_folder.exists():
        return False, f"Story '{story_title}' already exists in series '{series_name}'"
    
    series_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(story_folder), str(target_folder))
    
    new_story_path = target_folder / story_path.name
    add_story_to_series(series_name, story_title, story_type, reference, new_story_path)
    
    try:
        old_rel = story_path.relative_to(Path(SERIES_DIR))
        old_series_name = old_rel.parts[0]
        if old_series_name != series_name:
            old_meta = load_series_metadata(old_series_name)
            if old_meta:
                old_meta["stories"] = [s for s in old_meta["stories"] if s["title"] != story_title]
                save_series_metadata(old_series_name, old_meta)
    except ValueError:
        pass
    
    meta_file = target_folder / f"{story_title}_metadata.json"
    if meta_file.exists():
        with open(meta_file, 'r') as f:
            meta = json.load(f)
        meta["story_type"] = story_type
        meta["reference_story"] = str(reference) if reference else None
        with open(meta_file, 'w') as f:
            json.dump(meta, f, indent=2)
    
    return True, target_folder

def get_worldbook_metadata(worldbook_path):
    meta_path = worldbook_path.with_suffix('.meta.json')
    if meta_path.exists():
        with open(meta_path, 'r') as f:
            return json.load(f)
    return {
        "name": worldbook_path.stem,
        "linked_series": []
    }

def save_worldbook_metadata(worldbook_path, metadata):
    meta_path = worldbook_path.with_suffix('.meta.json')
    with open(meta_path, 'w') as f:
        json.dump(metadata, f, indent=2)

def update_worldbook_series_link(worldbook_path, series_name):
    if not worldbook_path or not series_name:
        return
    meta = get_worldbook_metadata(worldbook_path)
    if series_name not in meta["linked_series"]:
        meta["linked_series"].append(series_name)
    save_worldbook_metadata(worldbook_path, meta)

def update_job_status(job_id, status, progress=0, message="", files=None, title=None, job_type="story", errors=None, params=None):
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    
    if params is None and status_file.exists():
        try:
            with open(status_file, 'r') as f:
                old_data = json.load(f)
                params = old_data.get('params')
        except:
            pass

    with open(status_file, 'w') as f:
        json.dump({
            "job_id": job_id,
            "job_type": job_type,
            "status": status,
            "progress": progress,
            "message": message,
            "files": files or [],
            "title": title,
            "errors": errors or [],
            "params": params,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }, f, indent=2)

def get_job_status(job_id):
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    if status_file.exists():
        try:
            with open(status_file, 'r') as f:
                content = f.read()
                if not content.strip():
                    return None
                return json.loads(content)
        except Exception:
            return None
    return None

def get_all_jobs():
    jobs = []
    if not Path(JOBS_DIR).exists():
        return jobs
    for status_file in Path(JOBS_DIR).glob("job_*_status.json"):
        try:
            with open(status_file, 'r') as f:
                jobs.append(json.load(f))
        except:
            pass
    return jobs

def delete_job(job_id):
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    if status_file.exists():
        status_file.unlink()

def cleanup_old_jobs(keep_last=5):
    jobs = get_all_jobs()
    finished_jobs = [j for j in jobs if j['status'] in ['completed', 'error']]
    finished_jobs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    for job in finished_jobs[keep_last:]:
        delete_job(job['job_id'])

def is_cancel_requested(job_id):
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    if status_file.exists():
        try:
            with open(status_file, 'r') as f:
                content = f.read()
                if content.strip():
                    data = json.loads(content)
                    return data.get('cancel_requested', False)
        except:
            pass
    return False

def request_cancel(job_id):
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    if status_file.exists():
        try:
            with open(status_file, 'r') as f:
                data = json.loads(f)
            data['cancel_requested'] = True
            with open(status_file, 'w') as f:
                json.dump(data, f, indent=2)
        except:
            pass

def stream_llm_with_retry(prompt, model, max_tokens, temperature, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = llm_client.chat.completions.create(
                model=model, 
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens, 
                temperature=temperature, 
                stream=True
            )
            return response
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[WARN] LLM Connection error on attempt {attempt+1}, retrying in 5 seconds... ({e})")
                time.sleep(5)
            else:
                raise e

def run_generation_worker(job_id, topic, genre, story_type, reference_story, series_name, worldbook_path, features, length_instruction, want_tts, debug_mode, quick_test=False):
    cleanup_old_jobs(keep_last=3)
    try:
        def check_cancel():
            if is_cancel_requested(job_id):
                update_job_status(job_id, "error", 0, "Generation cancelled by user")
                status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
                os.remove(status_file)
                st.rerun()
                return True
            return False
        
        if not topic or not topic.strip():
            topic = "a compelling story of your choice"
        if not genre or not genre.strip():
            genre = "AI decides"
        
        if quick_test:
            length_instruction = "Write EXACTLY ONE SHORT chapter. Do not write more than 1 chapter. This is just a test, so no need to fo all out!"
            if topic == "a compelling story of your choice":
                topic = "A story about you, and your LLM came to be."
            update_job_status(job_id, "running", 0, "Quick Test Mode: Generating 1 chapter...")
        
        params = {
            "topic": topic, "genre": genre, "story_type": story_type,
            "reference_story": str(reference_story) if reference_story else None,
            "series_name": series_name, "worldbook_path": str(worldbook_path) if worldbook_path else None,
            "features": features, "length_instruction": length_instruction,
            "want_tts": want_tts, "debug_mode": debug_mode, "quick_test": quick_test
        }
        update_job_status(job_id, "running", 0, "Starting generation...", job_type="story", params=params)
        base_prompt = read_base_prompt()
        
        character_voices = {}
        if worldbook_path:
            with open(worldbook_path, 'r') as f:
                wb_content = f.read()
            character_voices = extract_character_voices(wb_content)
        
        available_sfx = get_available_sfx()
        voice_instruction = build_voice_instruction(character_voices if character_voices else None, available_sfx)
        story_context = load_story_context(reference_story, job_id)
        worldbook_context = load_worldbook_context(worldbook_path)

        if debug_mode:
            story = TEST_STORY
            title = "Debug Test Story"
            update_job_status(job_id, "running", 0.1, "Debug mode: Loaded test story.", title=title)
            book_summary = ""
        else:
            if quick_test:
                length_instruction = "Write EXACTLY ONE chapter. Do not write more than 1 chapter."
                if topic == "a compelling story of your choice":
                    topic = "a very short story about a robot learning to paint"
                update_job_status(job_id, "running", 0, "Quick Test Mode: Generating 1 chapter...")
            
            update_job_status(job_id, "running", 0, "Phase 1: Generating Outline...")
            features_instruction = f"The story MUST include these elements: {', '.join(features)}. " if features else ""
            type_instruction = ""
            if story_type == "sequel":
                type_instruction = "This is a SEQUEL - continue the story logically from previous events while introducing new conflicts."
            elif story_type == "prequel":
                type_instruction = "This is a PREQUEL - explore events leading up to referenced story with established characters/settings."
            
            prompt = f"""{base_prompt}
{worldbook_context}{story_context}{voice_instruction}
Generate a detailed story outline.
Topic: {topic if topic else 'a compelling story of your choice'}
Genre: {genre if genre else 'AI decides'}
{type_instruction}
{features_instruction}
Length requirement: {length_instruction}
List each chapter with a brief description."""
            
            if check_cancel(): return
            response = llm_client.chat.completions.create(
                model=STORY_MODEL, messages=[{"role": "user", "content": prompt}],
                max_tokens=1500, temperature=0.8, stream=True
            )
            
            outline = ""
            token_count = 0
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta:
                    content = chunk.choices[0].delta.content
                    if content:
                        outline += content
                        token_count += 1
                        if token_count % 15 == 0:
                            update_job_status(job_id, "running", min(0.05, token_count / 1000), f"Phase 1: Generating Outline... {token_count} tokens")
            
            update_job_status(job_id, "running", 0.1, f"Phase 1: Outline Complete ({token_count} tokens)")
            chapter_matches = re.findall(r'(?:Chapter|chapter)\s+(\d+)', outline, re.IGNORECASE)
            if quick_test:
                total_chapters = 1
            else:         
                total_chapters = max([int(x) for x in chapter_matches]) if chapter_matches else 10
            update_job_status(job_id, "running", 0.1, f"Detected {total_chapters} chapters. Starting Phase 2...")

            story_parts = []
            chapter_summaries = []
            
            for chapter_num in range(1, total_chapters + 1):
                if check_cancel(): return
                chapter_progress = 0.1 + (chapter_num - 1) / total_chapters * 0.7
                update_job_status(job_id, "running", chapter_progress, f"Phase 2: Writing Chapter {chapter_num}/{total_chapters}...")

                running_summary = build_running_summary(chapter_summaries)
                
                if chapter_num == 1:
                    ch_prompt = f"""{base_prompt}
{worldbook_context}{story_context}{voice_instruction}
Based on this outline:
{outline}
Write Chapter {chapter_num} in detail. Wrap ALL dialogue AND narration in voice tags as described in the voice instructions above."""
                else:
                    ch_prompt = f"""{base_prompt}
{worldbook_context}{story_context}{voice_instruction}
{running_summary}
Continue the story logically from the summaries above. 
Write Chapter {chapter_num} in detail. Wrap ALL dialogue AND narration in voice tags as described in the voice instructions above."""
                ch_prompt += " End this chapter with [END]"
                
                update_job_status(job_id, "running", chapter_progress, f"Phase 2: Requesting Chapter {chapter_num}/{total_chapters} from AI...")
                response = stream_llm_with_retry(
                    prompt=ch_prompt, 
                    model=STORY_MODEL, 
                    max_tokens=2048, 
                    temperature=0.8
                )
                
                chapter = ""
                ch_tokens = 0
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta:
                        content = chunk.choices[0].delta.content
                        if content:
                            chapter += content
                            ch_tokens += 1
                
                story_parts.append(chapter)

                update_job_status(job_id, "running", chapter_progress, 
                                 f"Chapter {chapter_num}/{total_chapters} written ({ch_tokens} tokens). Summarizing...")
                chapter_summary = generate_chapter_summary(chapter, chapter_num, job_id)
                chapter_summaries.append(chapter_summary)

                chapter_progress = 0.1 + chapter_num / total_chapters * 0.7
                update_job_status(job_id, "running", chapter_progress, f"Chapter {chapter_num}/{total_chapters} Completed ({ch_tokens} tokens)")
            
            story = "\n\n".join(story_parts)

            if not story.strip():
                update_job_status(job_id, "error", 0, "Failed to generate story - empty response from AI")
                return
            
            update_job_status(job_id, "running", 0.85, "Phase 3: Generating Title...")
            if check_cancel(): return
            try:
                title_prompt = f"Based on the following story outline, create ONE compelling title:\n========\n{outline}\n========\nONLY OUTPUT THE TITLE, NOTHING ELSE!"
                title_response = llm_client.chat.completions.create(
                    model=TITLE_MODEL, messages=[{"role": "user", "content": title_prompt}],
                    max_tokens=30, temperature=0.7
                )
                title = title_response.choices[0].message.content.strip()
                
                title = re.sub(r'^[*_`#]*(?:Book\s+)?Title[*_`#]*\s*[:\-–]\s*', '', title, flags=re.IGNORECASE)
                title = re.sub(r'[*`#]+', '', title)
                title = title.replace('_', ' ')
                title = title.strip('"\'""''')
                title = ' '.join(title.split())
                title = title[:50].strip()
            except:
                title = "Untitled-Story"
            update_job_status(job_id, "running", 0.9, f"Generated Title: {title}", title=title)

        update_job_status(job_id, "running", 0.9, "Saving files...", title=title)
        filepath, story_dir = save_story(story, title, series_name)
        tts_filepath = save_tts_story(story, title, story_dir)
        voices_used = extract_voices_used(story)
        save_metadata(title, story_type, reference_story, worldbook_path, features, story_dir, voices_used)

        if not debug_mode:
            if chapter_summaries:
                summaries_path = story_dir / f"{sanitize_title(title)}_chapter_summaries.json"
                with open(summaries_path, 'w') as f:
                    json.dump(chapter_summaries, f, indent=2)
                update_job_status(job_id, "running", 0.92, "Generating book summary from chapter summaries...")
                book_summary = generate_book_summary_from_chapters(chapter_summaries, title, story_dir, job_id)
                update_job_status(job_id, "running", 0.93, "Book summary generated!")
            
        if series_name:
            add_story_to_series(series_name, title, story_type, reference_story, filepath,
                            worldbook_path.name if worldbook_path else None)
            if worldbook_path:
                update_worldbook_series_link(worldbook_path, series_name)
        
        files = [str(filepath), str(story_dir / f"{sanitize_title(title)}.pdf"), str(tts_filepath)]

        if want_tts:
            if check_cancel(): return
            audiobook_path = generate_tts_background(story, title, story_dir, job_id)
            if audiobook_path:
                files.append(str(audiobook_path))
                cover_path = generate_cover_image(title, book_summary if not debug_mode else "", story_dir, job_id)
                if cover_path:
                    embed_cover_in_mp3(str(audiobook_path), str(cover_path), title)
                    files.append(str(cover_path))
                    update_job_status(job_id, "running", 0.97, "Cover art embedded in audiobook!")
        
        update_job_status(job_id, "completed", 1.0, "Generation Complete!", files, title)
        
    except Exception as e:
        update_job_status(job_id, "error", 0, str(e))

def run_tts_worker(job_id, story_path):
    try:
        params = {"story_path": str(story_path)}
        update_job_status(job_id, "running", 0, "Loading story...", job_type="tts", params=params)
        
        tts_path = story_path.parent / f"{story_path.stem}_tts.txt"
        if tts_path.exists():
            with open(tts_path, 'r') as f:
                story_content = f.read()
        else:
            with open(story_path, 'r') as f:
                story_content = f.read()
        
        audiobook_path = generate_tts_background(story_content, story_path.stem, story_path.parent, job_id)
        
        if audiobook_path:
            update_job_status(job_id, "completed", 1.0, "TTS Generation Complete!", [str(audiobook_path)], story_path.stem, job_type="tts")
        else:
            update_job_status(job_id, "error", 0, "TTS generation failed", job_type="tts")
    except Exception as e:
        print(e)
        update_job_status(job_id, "error", 0, str(e), job_type="tts")

def run_clean_worker(job_id, story_path):
    try:
        params = {"story_path": str(story_path)}
        update_job_status(job_id, "running", 0, "Loading story...", job_type="clean", params=params)
        
        with open(story_path, 'r') as f:
            story_content = f.read()
        
        update_job_status(job_id, "running", 0.5, "Removing voice tags...", title=story_path.stem)
        clean_content = remove_voice_tags(story_content)
        clean_filepath = story_path.parent / f"{story_path.stem}_cleaned{story_path.suffix}"
        
        with open(clean_filepath, 'w') as f:
            f.write(clean_content)
        
        update_job_status(job_id, "completed", 1.0, "Story cleaned successfully!", [str(clean_filepath)], story_path.stem, job_type="clean")
    except Exception as e:
        print(e)
        update_job_status(job_id, "error", 0, str(e), job_type="clean")

def validate_mp3(file_path):
    try:
        audio = AudioSegment.from_mp3(file_path)
        if len(audio) > 0:
            return True
    except Exception:
        pass
    return False

def generate_tts_background(story_text, title, story_dir, job_id):
    """Generate TTS using Kokoro's native multi-speaker support with SFX timeline fusion"""
    safe_title = sanitize_title(title)
    tts_dir = story_dir / f"{safe_title}_tts_segments"
    tts_dir.mkdir(parents=True, exist_ok=True)

    story_text = fix_sfx_tags(story_text)
    story_text = preprocess_sfx_in_voice_tags(story_text)

    voice_aliases = extract_mixed_voices(story_text)
    
    SPLIT_PATTERN = re.compile(r'(\x5Bsfx:[a-z0-9_]+\x5D|\x5Bbgsfx:[a-z0-9_]+\x5D|\x5B/bgsfx\x5D)', re.IGNORECASE)
    parts = [p for p in SPLIT_PATTERN.split(story_text) if p.strip()]
    
    timeline = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        sfx_match = re.match(r'\x5Bsfx:([a-z0-9_]+)\x5D', part, re.IGNORECASE)
        bgsfx_start_match = re.match(r'\x5Bbgsfx:([a-z0-9_]+)\x5D', part, re.IGNORECASE)
        
        if sfx_match:
            timeline.append({'type': 'sfx', 'name': sfx_match.group(1).lower()})
        elif bgsfx_start_match:
            timeline.append({'type': 'bgsfx_start', 'name': bgsfx_start_match.group(1).lower()})
        elif part.lower() == '[/bgsfx]':
            timeline.append({'type': 'bgsfx_stop'})
        else:
            kokoro_text = convert_to_kokoro_format(part, voice_aliases)
            kokoro_text = re.sub(r'^#{1,6}\s+', '', kokoro_text, flags=re.MULTILINE)
            kokoro_text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', kokoro_text)
            
            paragraphs = [p.strip() for p in kokoro_text.split('\n\n') if p.strip()]
            for para in paragraphs:
                if len(para) > 3:
                    timeline.append({'type': 'tts', 'text': para})
    
    if not timeline:
        update_job_status(job_id, "error", 0, "No text content found for TTS")
        return None
    
    audio_items = []
    tts_count = sum(1 for item in timeline if item['type'] == 'tts')
    processed_tts = 0
    errors = []
    max_retries = 3
    
    update_job_status(job_id, "running", 0.9, f"TTS Generation: 0/{tts_count} segments")
    
    for item in timeline:
        if is_cancel_requested(job_id):
            update_job_status(job_id, "error", 0, "TTS generation cancelled by user")
            return None
            
        if item['type'] == 'tts':
            audio_file = tts_dir / f"segment_{processed_tts:04d}.mp3"
            success = False
            progress = 0.9 + (processed_tts + 1) / tts_count * 0.08
            
            for attempt in range(max_retries):
                try:
                    tts_response = requests.post(
                        f"{TTS_URL}/audio/speech",
                        json={
                            "model": "kokoro",
                            "voice": "af_heart",
                            "input": item['text'],
                            "allow_voice_tags": True,
                            "voice_aliases": voice_aliases,
                            "response_format": "mp3"
                        },
                        headers={"Authorization": f"Bearer {os.getenv('TTS_API_KEY', 'not-needed')}"},
                        stream=True,
                        timeout=600
                    )

                    if tts_response.status_code == 200:
                        with open(str(audio_file), 'wb') as f:
                            for chunk in tts_response.iter_content(chunk_size=8192):
                                f.write(chunk)
                    else:
                        raise Exception(f"API {tts_response.status_code}: {tts_response.text[:100]}")
                    
                    if validate_mp3(str(audio_file)):
                        audio_items.append({'type': 'tts', 'path': str(audio_file)})
                        success = True
                        break
                    else:
                        if audio_file.exists(): audio_file.unlink()
                except Exception as e:
                    if audio_file.exists(): audio_file.unlink()
                    if attempt < max_retries - 1:
                        time.sleep(1)
                    else:
                        errors.append(f"Segment {processed_tts}: {e}")
            
            processed_tts += 1
            msg = f"TTS Generation: {processed_tts}/{tts_count} segments"
            if errors: msg += f" [{len(errors)} errors]"
            update_job_status(job_id, "running", progress, msg)
        else:
            audio_items.append(item)
    
    # FUSION STAGE (Hybrid Memory-Safe Approach)
    update_job_status(job_id, "running", 0.98, "Fusing audio with SFX timeline...")
    
    audiobook_path = story_dir / f"{safe_title}_audiobook.mp3"
    temp_dir = story_dir / "temp_chunks"
    temp_dir.mkdir(exist_ok=True)
    
    pause_tts = AudioSegment.silent(duration=800)
    pause_sfx = AudioSegment.silent(duration=200)
    
    chunk_size = 100
    chunk_files = []
    chunk_num = 0
    
    current_bgsfx = None
    bgsfx_offset = 0
    
    for i in range(0, len(audio_items), chunk_size):
        chunk_items = audio_items[i:i+chunk_size]
        chunk_audio = AudioSegment.silent(duration=100)
        
        for j, item in enumerate(chunk_items):
            if is_cancel_requested(job_id):
                update_job_status(job_id, "error", 0, "Fusion cancelled by user")
                return None
                
            if item['type'] == 'tts':
                tts_audio = AudioSegment.from_mp3(item['path'])
                tts_duration = len(tts_audio)
                
                if current_bgsfx:
                    # MEMORY SAFE LOOP: Slice the original BGSFX using modulo math
                    bgsfx_len = len(current_bgsfx)
                    needed_duration = tts_duration
                    start_ms = bgsfx_offset % bgsfx_len
                    
                    bgsfx_slice = AudioSegment.empty()
                    current_pos = 0
                    while current_pos < needed_duration:
                        take = min(bgsfx_len - start_ms, needed_duration - current_pos)
                        bgsfx_slice += current_bgsfx[start_ms : start_ms + take]
                        current_pos += take
                        start_ms = 0 # Next loop starts from beginning
                        
                    bgsfx_slice = bgsfx_slice - 22
                    mixed = tts_audio.overlay(bgsfx_slice)
                    chunk_audio += mixed
                else:
                    chunk_audio += tts_audio
                bgsfx_offset += tts_duration
                
            elif item['type'] == 'sfx':
                sfx_path = get_sfx_path(item['name'])
                if sfx_path and sfx_path.exists():
                    try:
                        sfx = AudioSegment.from_mp3(str(sfx_path)) - 8
                        sfx_duration = len(sfx)
                        
                        if current_bgsfx:
                            # MEMORY SAFE LOOP
                            bgsfx_len = len(current_bgsfx)
                            needed_duration = sfx_duration
                            start_ms = bgsfx_offset % bgsfx_len
                            
                            bgsfx_slice = AudioSegment.empty()
                            current_pos = 0
                            while current_pos < needed_duration:
                                take = min(bgsfx_len - start_ms, needed_duration - current_pos)
                                bgsfx_slice += current_bgsfx[start_ms : start_ms + take]
                                current_pos += take
                                start_ms = 0
                                
                            bgsfx_slice = bgsfx_slice - 22
                            mixed_sfx = sfx.overlay(bgsfx_slice)
                            chunk_audio += mixed_sfx
                        else:
                            chunk_audio += sfx
                        bgsfx_offset += sfx_duration
                    except Exception as e:
                        print(f"[ERROR] SFX load failed: {e}")
                else:
                    print(f"[WARN] SFX not found: {item['name']}")
                    
            elif item['type'] == 'bgsfx_start':
                sfx_path = get_sfx_path(item['name'])
                if sfx_path and sfx_path.exists():
                    try:
                        current_bgsfx = AudioSegment.from_mp3(str(sfx_path))
                    except Exception as e:
                        print(f"[ERROR] BGSFX load failed: {e}")
                        current_bgsfx = None
                else:
                    print(f"[WARN] BGSFX not found: {item['name']}")
                    
            elif item['type'] == 'bgsfx_stop':
                current_bgsfx = None
                
            if j < len(chunk_items) - 1:
                pause_dur = pause_tts if item['type'] == 'tts' else pause_sfx
                
                if current_bgsfx:
                    # MEMORY SAFE LOOP FOR PAUSES
                    bgsfx_len = len(current_bgsfx)
                    needed_duration = len(pause_dur)
                    start_ms = bgsfx_offset % bgsfx_len
                    
                    bgsfx_slice = AudioSegment.empty()
                    current_pos = 0
                    while current_pos < needed_duration:
                        take = min(bgsfx_len - start_ms, needed_duration - current_pos)
                        bgsfx_slice += current_bgsfx[start_ms : start_ms + take]
                        current_pos += take
                        start_ms = 0
                        
                    bgsfx_slice = bgsfx_slice - 22
                    chunk_audio += bgsfx_slice
                else:
                    chunk_audio += pause_dur
                bgsfx_offset += len(pause_dur)
        
        chunk_path = temp_dir / f"chunk_{chunk_num:04d}.mp3"
        chunk_audio.export(str(chunk_path), format="mp3")
        chunk_files.append(chunk_path)
        chunk_num += 1
        
        del chunk_audio
        gc.collect()
        
        progress = 0.98 + (i + chunk_size) / len(audio_items) * 0.01
        update_job_status(job_id, "running", progress, 
                         f"Fusing audio: chunk {chunk_num} ({i+chunk_size}/{len(audio_items)} segments)")
        
    update_job_status(job_id, "running", 0.99, "Finalizing audiobook with FFmpeg...")
    
    list_file = story_dir / "ffmpeg_list.txt"
    with open(list_file, 'w') as f:
        for chunk_path in chunk_files:
            f.write(f"file '{chunk_path}'\n")
    
    cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', str(list_file),
        '-c:a', 'libmp3lame', '-b:a', '128k',
        str(audiobook_path)
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except Exception as e:
        update_job_status(job_id, "error", 0, f"Failed to export audiobook: {e}")
        return None
    
    list_file.unlink()
    for chunk_path in chunk_files:
        chunk_path.unlink()
    temp_dir.rmdir()
    
    cleanup_success = False
    for attempt in range(3):
        try:
            shutil.rmtree(tts_dir)
            cleanup_success = True
            break
        except Exception:
            time.sleep(1)
    
    if not cleanup_success:
        try:
            for f in tts_dir.glob("*"):
                try: f.unlink()
                except: pass
            tts_dir.rmdir()
        except: pass
    
    total_errors = len(errors)
    if total_errors > 0:
        update_job_status(job_id, "running", 0.99, 
                         f"Audiobook exported with {total_errors} errors (skipped bad segments)")
    
    return audiobook_path

def generate_cover_image(title, story_summary, story_dir, job_id=None):
    if job_id:
        update_job_status(job_id, "running", 0.95, "Generating cover art...")
    
    prompt = f"Book cover art for a story titled '{title}'. Style: atmospheric, cinematic, no text. Story summary: {story_summary[:300]}"
    
    try:
        response = requests.post(
            f"{IMG_URL}/images/generations",
            json={
                "model": IMG_MODEL,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json"
            },
            headers={"Authorization": f"Bearer {os.getenv('IMG_API_KEY', 'not-needed')}"},
            timeout=120
        )
        
        if response.status_code == 200:
            import base64
            image_data = base64.b64decode(response.json()['data'][0]['b64_json'])
            cover_path = story_dir / f"{sanitize_title(title)}_cover.png"
            with open(cover_path, 'wb') as f:
                f.write(image_data)
            return cover_path
    except Exception as e:
        print(f"[ERROR] Cover generation failed: {e}")
    return None

def generate_book_summary_from_chapters(chapter_summaries, title, story_dir, job_id=None):
    if not chapter_summaries:
        return ""
    
    combined = "\n\n".join([f"Chapter {i+1}: {summary}" for i, summary in enumerate(chapter_summaries)])
    
    if job_id:
        update_job_status(job_id, "running", 0.88, "Generating book summary from chapter summaries...")
    
    prompt = f"""Below are chapter summaries from a story. Combine them into ONE cohesive summary of maximum 600 words. Include:
- Main characters and their relationships
- Key plot points and events
- Important settings/locations
- How the story ends
- Any unresolved threads or cliffhangers

Chapter summaries:
{combined}

Final book summary (600 words max):"""
    
    response = llm_client.chat.completions.create(
        model=STORY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.3
    )
    
    summary = response.choices[0].message.content.strip()
    
    summary_path = story_dir / f"{sanitize_title(title)}_summary.txt"
    with open(summary_path, 'w') as f:
        f.write(summary)
    
    return summary

def check_auth():
    if 'authenticated' not in st.session_state:
        st.session_state['authenticated'] = False
    
    if st.session_state['authenticated']:
        return True
    
    st.markdown("## 🔐 Story Generator")
    st.markdown("Please enter the password to access the app.")
    
    with st.form("login_form"):
        password = st.text_input("Password", type="password", placeholder="Enter password...")
        submitted = st.form_submit_button("Login", type="primary")
        
        if submitted:
            expected_password = os.getenv("APP_PASSWORD", "")
            if password == expected_password and expected_password:
                st.session_state['authenticated'] = True
                st.rerun()
            else:
                st.error("❌ Incorrect password")
    
    return False

def main():
    st.set_page_config(page_title="Story Generator", page_icon="📖", layout="wide")
    ensure_directories()

    if not check_auth():
        return

    st.title("📖 Story Generator")
    st.markdown("Generate stories with AI, complete with multi-voice TTS audiobook generation.")

    with st.sidebar:
        if st.button("🚪 Logout"):
            st.session_state['authenticated'] = False
            st.rerun()

    jobs = get_all_jobs()
    running_jobs = [j for j in jobs if j['status'] == 'running']
    
    if running_jobs:
        st.sidebar.markdown("### ⚙️ Background Jobs")
        for job in running_jobs:
            job_id = job.get('job_id', 'unknown')
            title = job.get('title', 'Working...')
            job_type = job.get('job_type', 'story')
            
            with st.sidebar.container():
                st.markdown(f"**{job_type.title()}:** {title}")
                st.progress(job['progress'])
                st.caption(job['message'][:80] + ('...' if len(job['message']) > 80 else ''))
                
                if st.button("❌ Cancel", key=f"cancel_sidebar_{job_id}"):
                    request_cancel(job_id)
                    st.warning("Cancelling...")
                    time.sleep(1)
                    st.rerun()
                if st.button("❌ Force Delete Job", key=f"forcedel_sidebar_{job_id}"):
                    request_cancel(job_id)
                    st.warning("Force Deleting Job Task...")
                    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
                    os.remove(status_file)
                    time.sleep(3)
                    st.rerun()

                st.caption(f"Job ID: `{job_id}`")
                st.divider()
        
        time.sleep(2)
        st.rerun()

    menu = ["Generate New Story", "Job Status", "Generate TTS for Existing", "Story Library", "Series Manager", "Worldbook Manager", "Feature Manager", "Clean Existing Story"]
    choice = st.sidebar.selectbox("Menu", menu)

    if choice == "Generate New Story":
        generate_new_story_page()
    elif choice == "Job Status":
        job_status_page()
    elif choice == "Generate TTS for Existing":
        generate_tts_existing_page()
    elif choice == "Story Library":
        story_library_page()
    elif choice == "Series Manager":
        series_manager_page()
    elif choice == "Worldbook Manager":
        worldbook_manager_page()
    elif choice == "Feature Manager":
        feature_manager_page()
    elif choice == "Clean Existing Story":
        clean_existing_story_page()

def randomize_features():
    features = load_features()
    num_to_select = min(5, len(features))
    st.session_state['selected_features'] = random.sample(features, num_to_select)

def generate_new_story_page():
    st.header("Generate New Story")
    
    if 'current_job_id' in st.session_state:
        job = get_job_status(st.session_state['current_job_id'])
        if job:
            if job['status'] == 'running':
                st.info(f"🔄 **Active Job:** {job['message']}")
                st.progress(job['progress'])
                
                if st.button("❌ Cancel Generation", type="secondary"):
                    request_cancel(st.session_state['current_job_id'])
                    st.warning("Cancellation requested. Job will stop at next checkpoint...")
                    time.sleep(2)
                    st.rerun()
                
                time.sleep(2)
                st.rerun()
            elif job['status'] == 'completed':
                st.success(f"✅ **Last Job Complete!** {job['message']}")
                if job.get('files'):
                    st.write("**Generated files:**")
                    for file_path in job['files']:
                        p = Path(file_path)
                        if p.exists():
                            if p.suffix == '.txt':
                                with open(p, 'rb') as f:
                                    st.download_button(f"Download {p.name}", f, file_name=p.name)
                            elif p.suffix == '.pdf':
                                with open(p, 'rb') as f:
                                    st.download_button(f"Download {p.name}", f, file_name=p.name, mime='application/pdf')
                            elif p.suffix == '.mp3':
                                with open(p, 'rb') as f:
                                    st.download_button(f"Download {p.name}", f, file_name=p.name, mime='audio/mpeg')
                if st.button("Clear and Start New"):
                    delete_job(st.session_state['current_job_id'])
                    del st.session_state['current_job_id']
                    st.rerun()
                return
            elif job['status'] == 'error':
                st.error(f"❌ **Last Job Failed:** {job['message']}")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("🔄 Retry"):
                        params = job.get('params', {})
                        new_job_id = str(int(time.time()))
                        
                        ref_story = Path(params['reference_story']) if params.get('reference_story') else None
                        wb_path = Path(params['worldbook_path']) if params.get('worldbook_path') else None
                        
                        thread = threading.Thread(
                            target=run_generation_worker,
                            args=(new_job_id, params.get('topic'), params.get('genre'), params.get('story_type'), 
                                  ref_story, params.get('series_name'), wb_path, params.get('features', []), 
                                  params.get('length_instruction'), params.get('want_tts', True), params.get('debug_mode', False), params.get('quick_test', False))
                        )
                        thread.daemon = True
                        thread.start()
                        
                        delete_job(st.session_state['current_job_id'])
                        st.session_state['current_job_id'] = new_job_id
                        st.rerun()
                with col2:
                    if st.button("Clear Error"):
                        delete_job(st.session_state['current_job_id'])
                        del st.session_state['current_job_id']
                        st.rerun()
                return
    
    col1, col2 = st.columns(2)
    
    with col1:
        topic = st.text_input("Topic", placeholder="Leave blank for AI to decide")
        genre = st.text_input("Genre", placeholder="Leave blank for AI to decide")
        story_type = st.selectbox("Story Type", ["standalone", "sequel", "prequel"])
        
        reference_story = None
        series_name = None
        
        if story_type in ["sequel", "prequel"]:
            all_series = get_all_series()
            if not all_series:
                st.warning("No series exist yet. Generate a standalone story first or create a series in the Series Manager.")
            else:
                series_opts = [s["name"] for s in all_series]
                selected_series = st.selectbox("Select Series", series_opts)
                series_name = selected_series
                
                series_meta = load_series_metadata(selected_series)
                if series_meta and series_meta["stories"]:
                    story_opts = [f"{s['title']} (Book {s['order']})" for s in sorted(series_meta["stories"], key=lambda x: x["order"])]
                    ref_choice = st.selectbox("Reference Story", story_opts)
                    ref_title = ref_choice.split(" (Book")[0]
                    ref_story_meta = next(s for s in series_meta["stories"] if s["title"] == ref_title)
                    reference_story = Path(SERIES_DIR) / selected_series / ref_story_meta["path"]
                    if not reference_story.exists():
                        st.error(f"Reference story file not found: {reference_story}")
                        reference_story = None
                else:
                    st.info("No stories in this series yet. This will be the first story.")
        else:
            is_series = st.checkbox("Part of a series?")
            if is_series:
                all_series = get_all_series()
                series_opts = ["Create New Series"] + [s["name"] for s in all_series]
                s_choice = st.selectbox("Select Series", series_opts)
                if s_choice == "Create New Series":
                    series_name = st.text_input("New Series Name")
                else:
                    series_name = s_choice

    with col2:
        worldbooks = get_worldbooks()
        wb_opts = ["None"] + [wb.stem for wb in worldbooks]
        wb_choice = st.selectbox("Worldbook", wb_opts)
        worldbook_path = next((wb for wb in worldbooks if wb.stem == wb_choice), None) if wb_choice != "None" else None
        
        if worldbook_path:
            wb_meta = get_worldbook_metadata(worldbook_path)
            if wb_meta["linked_series"]:
                st.info(f"Worldbook linked to series: {', '.join(wb_meta['linked_series'])}")
            
            with open(worldbook_path, 'r') as f:
                wb_content = f.read()
            char_voices = extract_character_voices(wb_content)
            if char_voices:
                st.info(f"Character voices found: {len(char_voices)}")
                with st.expander("View character voices"):
                    for char, voice in char_voices.items():
                        st.write(f"• {char}: {voice}")
        
        features = load_features()
        col_feat1, col_feat2 = st.columns([4, 1])
        with col_feat1:
            selected_features = st.multiselect("Required Features", features, key="selected_features")
        with col_feat2:
            st.write("")
            st.write("")
            st.button("🎲 Random 5", on_click=randomize_features)
        
        length_opts = {
            "AI decides": "Decide the optimal chapter count yourself",
            "Very Short (1-2 chapters)": "Keep it very short with 1-2 chapters total",
            "Short (3-5 chapters)": "Keep it short with 3-5 chapters total",
            "Medium (6-12 chapters)": "Make it medium length with 6-12 chapters total",
            "Long (15-20 chapters)": "Make it long with 15-20 chapters total",
            "Very Long (25-30 chapters)": "Make it very long with 25-30 chapters total"
        }
        length_choice = st.selectbox("Story Length", list(length_opts.keys()))
        length_instruction = length_opts[length_choice]
        
        want_tts = st.checkbox("Generate TTS Audiobook", value=True)
        gen_mode = st.selectbox("Generation Mode", ["Normal", "Quick Test (1 Chapter)", "Debug (Test Story)"])
        debug_mode = (gen_mode == "Debug (Test Story)")
        quick_test = (gen_mode == "Quick Test (1 Chapter)")
    
    if st.button("🚀 Generate Story", type="primary"):
        job_id = str(int(time.time()))
        
        thread = threading.Thread(
            target=run_generation_worker,
            args=(job_id, topic, genre, story_type, reference_story, series_name, worldbook_path, selected_features, length_instruction, want_tts, debug_mode, quick_test)
        ) 
        thread.daemon = True
        thread.start()
        
        st.session_state['current_job_id'] = job_id
        st.success(f"✅ Generation started in background! Job ID: {job_id}")
        time.sleep(2)
        st.rerun()

def job_status_page():
    st.header("⚙️ Background Jobs")
    
    jobs = get_all_jobs()
    
    if not jobs:
        st.info("No background jobs found.")
        return

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🗑️ Clear All Finished"):
            for job in jobs:
                if job['status'] in ['completed', 'error']:
                    delete_job(job['job_id'])
            st.rerun()
    
    jobs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    running_jobs = [j for j in jobs if j['status'] == 'running']
    if running_jobs:
        st.info(f"🔄 {len(running_jobs)} job(s) running. Auto-refreshing in 2 seconds...")
        time.sleep(2)
        st.rerun()
    
    for job in jobs:
        job_id = job.get('job_id', 'unknown')
        job_type = job.get('job_type', 'story')
        title = job.get('title', 'In Progress...')
        
        status_icon = {
            'running': '🔄',
            'completed': '✅',
            'error': '❌'
        }.get(job['status'], '❓')
        
        with st.expander(f"{status_icon} Job {job_id} - {title} ({job['status'].title()})", expanded=(job['status'] == 'running')):
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st.write(f"**Type:** {job_type.title()}")
                st.write(f"**Status:** {job['status'].title()}")
                st.write(f"**Message:** {job['message']}")
                st.write(f"**Last Updated:** {job.get('timestamp', 'Unknown')}")
            
            with col2:
                if job['status'] == 'running':
                    st.metric("Progress", f"{job['progress']*100:.1f}%")
                elif job['status'] == 'completed':
                    st.metric("Files", len(job.get('files', [])))
                elif job['status'] == 'error':
                    st.metric("Error", "Failed")
            
            if job['status'] == 'running':
                st.progress(job['progress'])
                
                if st.button("❌ Cancel This Job", key=f"cancel_{job_id}"):
                    request_cancel(job_id)
                    st.warning("Cancellation requested...")
                    time.sleep(2)
                    st.rerun()
            elif job['status'] == 'completed':
                st.success("✅ Generation Complete!")
                for file_path in job.get('files', []):
                    p = Path(file_path)
                    if p.exists():
                        if p.suffix == '.txt':
                            with open(p, 'rb') as f:
                                st.download_button(f"Download {p.name}", f, file_name=p.name, key=f"dl_{job_id}_{p.name}")
                        elif p.suffix == '.pdf':
                            with open(p, 'rb') as f:
                                st.download_button(f"Download {p.name}", f, file_name=p.name, mime='application/pdf', key=f"dl_{job_id}_{p.name}")
                        elif p.suffix == '.mp3':
                            with open(p, 'rb') as f:
                                st.download_button(f"Download {p.name}", f, file_name=p.name, mime='audio/mpeg', key=f"dl_{job_id}_{p.name}")
                        elif p.suffix == '.png':
                            with open(p, 'rb') as f:
                                st.download_button(f"Download {p.name}", f, file_name=p.name, mime='image/png', key=f"dl_{job_id}_{p.name}")
                
                if st.button(f"Clear Job", key=f"clear_{job_id}"):
                    delete_job(job_id)
                    st.rerun()
            elif job['status'] == 'error':
                st.error(f"❌ Error: {job['message']}")
                
                col_retry, col_clear = st.columns(2)
                
                with col_retry:
                    if st.button(f"🔄 Retry Job", key=f"retry_{job_id}"):
                        new_job_id = str(int(time.time()))
                        params = job.get('params', {})
                        
                        if job_type == 'story':
                            ref_story = Path(params['reference_story']) if params.get('reference_story') else None
                            wb_path = Path(params['worldbook_path']) if params.get('worldbook_path') else None
                            
                            thread = threading.Thread(
                                target=run_generation_worker,
                                args=(new_job_id, params.get('topic'), params.get('genre'), params.get('story_type'), 
                                      ref_story, params.get('series_name'), wb_path, params.get('features', []), 
                                      params.get('length_instruction'), params.get('want_tts', True), params.get('debug_mode', False), params.get('quick_test', False))
                            )
                            thread.daemon = True
                            thread.start()
                            st.session_state['current_job_id'] = new_job_id
                            
                        elif job_type == 'tts':
                            story_path = Path(params['story_path'])
                            thread = threading.Thread(target=run_tts_worker, args=(new_job_id, story_path))
                            thread.daemon = True
                            thread.start()
                            st.session_state['current_tts_job_id'] = new_job_id
                            
                        elif job_type == 'clean':
                            story_path = Path(params['story_path'])
                            thread = threading.Thread(target=run_clean_worker, args=(new_job_id, story_path))
                            thread.daemon = True
                            thread.start()
                            st.session_state['current_clean_job_id'] = new_job_id
                            
                        elif job_type == 'summary':
                            story_path = Path(params['story_path'])
                            thread = threading.Thread(target=run_summary_worker, args=(new_job_id, story_path))
                            thread.daemon = True
                            thread.start()
                            
                        delete_job(job_id)
                        st.success(f"✅ Retrying job as {new_job_id}")
                        time.sleep(2)
                        st.rerun()
                
                with col_clear:
                    if st.button(f"Clear Failed Job", key=f"clear_err_{job_id}"):
                        delete_job(job_id)
                        st.rerun()

def generate_tts_existing_page():
    st.header("Generate TTS for Existing Story")
    
    if 'current_tts_job_id' in st.session_state:
        job = get_job_status(st.session_state['current_tts_job_id'])
        if job:
            if job['status'] == 'running':
                st.info(f"🔄 **Active Job:** {job['message']}")
                st.progress(job['progress'])
                time.sleep(2)
                st.rerun()
            elif job['status'] == 'completed':
                st.success(f"✅ **TTS Complete!** {job['message']}")
                if job.get('files'):
                    for file_path in job['files']:
                        p = Path(file_path)
                        if p.exists() and p.suffix == '.mp3':
                            with open(p, 'rb') as f:
                                st.download_button(f"Download {p.name}", f, file_name=p.name, mime='audio/mpeg')
                if st.button("Clear and Start New"):
                    delete_job(st.session_state['current_tts_job_id'])
                    del st.session_state['current_tts_job_id']
                    st.rerun()
                return
            elif job['status'] == 'error':
                st.error(f"❌ **Job Failed:** {job['message']}")
                if st.button("Clear Error"):
                    delete_job(st.session_state['current_tts_job_id'])
                    del st.session_state['current_tts_job_id']
                    st.rerun()
                return
    
    stories = get_all_stories()
    if not stories:
        st.warning("No stories found.")
        return
    
    story_opts = [str(s.relative_to(Path(OUTPUT_DIR).parent)) for s in stories]
    selected = st.selectbox("Select Story", story_opts)
    
    if st.button("Generate TTS", type="primary"):
        selected_file = next(s for s in stories if str(s.relative_to(Path(OUTPUT_DIR).parent)) == selected)
        
        job_id = f"tts_{int(time.time())}"
        
        thread = threading.Thread(
            target=run_tts_worker,
            args=(job_id, selected_file)
        )
        thread.daemon = True
        thread.start()
        
        st.session_state['current_tts_job_id'] = job_id
        st.success(f"✅ TTS generation started in background! Job ID: {job_id}")
        st.rerun()

def run_summary_worker(job_id, story_path):
    try:
        params = {"story_path": str(story_path)}
        update_job_status(job_id, "running", 0, "Loading story for summary...", job_type="summary", params=params)
        summary = generate_story_summary(story_path, job_id)
        summary_path = story_path.parent / f"{story_path.stem}_summary.txt"
        update_job_status(job_id, "completed", 1.0, "Summary generated successfully!", [str(summary_path)], story_path.stem, job_type="summary")
    except Exception as e:
        print(e)
        update_job_status(job_id, "error", 0, str(e), job_type="summary")

def story_library_page():
    st.header("📚 Story Library")
    stories = get_all_stories()
    if not stories:
        st.info("No stories found. Generate one first!")
        return
    
    story_opts = [str(s.relative_to(Path(OUTPUT_DIR).parent)) for s in stories]
    selected = st.selectbox("Select Story to View", story_opts)
    
    selected_file = next(s for s in stories if str(s.relative_to(Path(OUTPUT_DIR).parent)) == selected)
    
    with open(selected_file, 'r') as f:
        story_content = f.read()
    
    st.subheader(selected_file.stem)
    st.text_area("Content", story_content, height=500)

    st.divider()
    summary_path = selected_file.parent / f"{selected_file.stem}_summary.txt"
    
    if summary_path.exists():
        st.success("✅ Book summary exists. Ready for sequels!")
        with open(summary_path, 'r') as f:
            st.text_area("Summary Content", f.read(), height=200)
    else:
        st.warning("No book summary found. Generate one to speed up future sequels.")
        if st.button("Generate Book Summary", type="primary"):
            job_id = f"summary_{int(time.time())}"
            thread = threading.Thread(
                target=run_summary_worker,
                args=(job_id, selected_file)
            )
            thread.daemon = True
            thread.start()
            st.success(f"✅ Summary generation started in background! Job ID: {job_id}")
            time.sleep(2)
            st.rerun()
    
    audiobook_path = selected_file.parent / f"{selected_file.stem}_audiobook.mp3"
    if audiobook_path.exists():
        st.subheader("🎙️ Audiobook")
        st.audio(str(audiobook_path))
        
        with open(audiobook_path, "rb") as f:
            st.download_button("Download Audiobook", f, file_name=f"{selected_file.stem}_audiobook.mp3", mime="audio/mpeg")

def series_manager_page():
    st.header("📚 Series Manager")
    
    tab1, tab2, tab3 = st.tabs(["View Series", "Create New Series", "Add Existing Story"])
    
    with tab1:
        st.subheader("View All Series")
        all_series = get_all_series()
        
        if not all_series:
            st.info("No series found. Create one in the 'Create New Series' tab!")
        else:
            for series in all_series:
                with st.expander(f"📖 {series['name']} ({len(series['stories'])} stories)"):
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        st.write(f"**Worldbook:** {series.get('worldbook', 'None')}")
                        st.write(f"**Created:** {series.get('created', 'Unknown')}")
                        
                        if series.get('stories'):
                            st.write("**Stories (in order):**")
                            for story in sorted(series['stories'], key=lambda x: x["order"]):
                                st.write(f"{story['order']}. {story['title']} ({story['type']})")
                                if story.get('reference'):
                                    st.write(f"   ↳ References: {story['reference']}")
                        else:
                            st.write("*No stories yet*")
                    
                    with col2:
                        new_name = st.text_input("Rename", value=series['name'], key=f"rename_{series['name']}")
                        if st.button("Rename", key=f"rename_btn_{series['name']}"):
                            if new_name != series['name'] and new_name.strip():
                                old_path = Path(SERIES_DIR) / series['name']
                                new_path = Path(SERIES_DIR) / new_name
                                old_path.rename(new_path)
                                series['name'] = new_name
                                save_series_metadata(new_name, series)
                                st.success(f"Renamed to {new_name}")
                                st.rerun()
    
    with tab2:
        st.subheader("Create New Series")
        
        new_series_name = st.text_input("Series Name", key="new_series_name_input")
        
        worldbooks = get_worldbooks()
        wb_opts = ["None"] + [wb.stem for wb in worldbooks]
        wb_choice = st.selectbox("Link to Worldbook", wb_opts, key="new_series_wb")
        
        if st.button("Create Series", type="primary", key="create_series_btn"):
            if new_series_name and new_series_name.strip():
                series_dir = Path(SERIES_DIR) / new_series_name
                series_dir.mkdir(parents=True, exist_ok=True)
                
                meta = {
                    "name": new_series_name,
                    "worldbook": f"{wb_choice}.txt" if wb_choice != "None" else None,
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "stories": []
                }
                save_series_metadata(new_series_name, meta)
                
                if wb_choice != "None":
                    wb_path = next(wb for wb in worldbooks if wb.stem == wb_choice)
                    update_worldbook_series_link(wb_path, new_series_name)
                
                st.success(f"Series created: {new_series_name}")
                st.rerun()
            else:
                st.error("Series name is required.")
    
    with tab3:
        st.subheader("Add Existing Story to Series")
        
        stories = get_all_stories()
        if not stories:
            st.info("No stories found. Generate one first!")
            return
        
        all_series = get_all_series()
        if not all_series:
            st.info("No series exist yet. Create one in the 'Create New Series' tab.")
            return
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Select Story to Move:**")
            story_opts = [str(s.relative_to(Path(OUTPUT_DIR).parent)) for s in stories]
            selected_story = st.selectbox("Story", story_opts, key="move_story_select")
        
        with col2:
            st.write("**Select Target Series:**")
            series_opts = [s["name"] for s in all_series]
            selected_series = st.selectbox("Series", series_opts, key="move_series_select")
            
            series_meta = load_series_metadata(selected_series)
            if series_meta and series_meta.get('stories'):
                ref_opts = ["None"] + [f"{s['title']} (Book {s['order']})" for s in sorted(series_meta["stories"], key=lambda x: x["order"])]
                ref_choice = st.selectbox("Reference Story (optional)", ref_opts, key="move_ref_select")
            else:
                ref_choice = "None"
                st.info("No stories in this series yet.")
            
            story_type = st.selectbox("Story Type", ["standalone", "sequel", "prequel"], key="move_type_select")
        
        if st.button("Move Story to Series", type="primary", key="move_story_btn"):
            story_path = next(s for s in stories if str(s.relative_to(Path(OUTPUT_DIR).parent)) == selected_story)
            
            reference = None
            if ref_choice != "None":
                ref_title = ref_choice.split(" (Book")[0]
                ref_meta = next(s for s in series_meta["stories"] if s["title"] == ref_title)
                reference = Path(SERIES_DIR) / selected_series / ref_meta["path"]
            
            success, result = add_existing_story_to_series(story_path, selected_series, story_type, reference)
            
            if success:
                st.success(f"Story moved to series '{selected_series}'!")
                st.info(f"New location: {result}")
                st.rerun()
            else:
                st.error(result)

def worldbook_manager_page():
    st.header("🌍 Worldbook Manager")
    
    tab1, tab2 = st.tabs(["Create New", "Edit Existing"])
    
    with tab1:
        st.subheader("Create New Worldbook")
        name = st.text_input("Worldbook Name (filename)", key="new_wb_name")
        
        st.markdown("**Tip:** You can define character voices by adding a `[CHARACTER VOICES]` section at the end of your worldbook:")
        st.code("""[CHARACTER VOICES]
John Doe: am_adam
Jane Smith: af_bella
Narrator: af_heart""", language="text")
        
        content = st.text_area("Worldbook Content", height=300, key="new_wb_content",
                              placeholder="Enter world lore, locations, history, character descriptions...\n\n[CHARACTER VOICES]\nCharacter Name: voice_name")
        
        if st.button("Save Worldbook", type="primary", key="save_new_wb"):
            if name and content:
                worldbook_path = Path(WORLDBOOK_DIR) / f"{name}.txt"
                with open(worldbook_path, 'w') as f:
                    f.write(content)
                st.success(f"Worldbook saved: {worldbook_path}")
                st.rerun()
            else:
                st.error("Name and content are required.")
    
    with tab2:
        st.subheader("Edit Existing Worldbook")
        worldbooks = get_worldbooks()
        if not worldbooks:
            st.info("No worldbooks found.")
            return
        
        wb_opts = [wb.stem for wb in worldbooks]
        selected_wb = st.selectbox("Select Worldbook", wb_opts, key="edit_wb_select")
        
        wb_path = next(wb for wb in worldbooks if wb.stem == selected_wb)
        with open(wb_path, 'r') as f:
            current_content = f.read()
        
        wb_meta = get_worldbook_metadata(wb_path)
        if wb_meta["linked_series"]:
            st.info(f"**Linked series:** {', '.join(wb_meta['linked_series'])}")
        
        char_voices = extract_character_voices(current_content)
        if char_voices:
            st.success(f"**Character voices detected:** {len(char_voices)}")
            for char, voice in char_voices.items():
                st.write(f"• {char}: {voice}")
        else:
            st.warning("No [CHARACTER VOICES] section found. Add one to assign consistent voices to characters.")
        
        edited_content = st.text_area("Edit Content", current_content, height=400, key="edit_wb_content")
        
        if st.button("Update Worldbook", type="primary", key="update_wb"):
            with open(wb_path, 'w') as f:
                f.write(edited_content)
            st.success(f"Worldbook updated: {wb_path}")
            st.rerun()

def feature_manager_page():
    st.header("✨ Feature Manager")
    st.markdown("Edit the available story features. These will appear as checkboxes when generating a new story.")
    
    features = load_features()
    features_str = "\n".join(features)
    
    edited_features = st.text_area("Features (one per line)", features_str, height=300)
    
    if st.button("Save Features", type="primary"):
        new_features = [f.strip() for f in edited_features.split('\n') if f.strip()]
        with open(FEATURES_FILE, 'w') as f:
            f.write('\n'.join(new_features))
        st.success(f"Features saved! {len(new_features)} features available.")

def clean_existing_story_page():
    st.header("🧹 Clean Existing Story (Remove Voice Tags)")
    
    if 'current_clean_job_id' in st.session_state:
        job = get_job_status(st.session_state['current_clean_job_id'])
        if job:
            if job['status'] == 'running':
                st.info(f"🔄 **Active Job:** {job['message']}")
                st.progress(job['progress'])
                time.sleep(2)
                st.rerun()
            elif job['status'] == 'completed':
                st.success(f"✅ **Cleaning Complete!** {job['message']}")
                if job.get('files'):
                    for file_path in job['files']:
                        p = Path(file_path)
                        if p.exists():
                            with open(p, 'rb') as f:
                                st.download_button(f"Download {p.name}", f, file_name=p.name)
                if st.button("Clear and Start New"):
                    delete_job(st.session_state['current_clean_job_id'])
                    del st.session_state['current_clean_job_id']
                    st.rerun()
                return
            elif job['status'] == 'error':
                st.error(f"❌ Error: {job['message']}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(f"🔄 Retry Job", key=f"retry_{job_id}"):
                        new_job_id = str(int(time.time()))
                        params = job.get('params', {})
                        
                        if job['job_type'] == 'story':
                            ref_story = Path(params['reference_story']) if params.get('reference_story') else None
                            wb_path = Path(params['worldbook_path']) if params.get('worldbook_path') else None
                            
                            thread = threading.Thread(
                                target=run_generation_worker,
                                args=(new_job_id, params.get('topic'), params.get('genre'), params.get('story_type'), 
                                      ref_story, params.get('series_name'), wb_path, params.get('features', []), 
                                      params.get('length_instruction'), params.get('want_tts', True), params.get('debug_mode', False), params.get('quick_test', False))
                            )
                            thread.daemon = True
                            thread.start()
                            st.session_state['current_job_id'] = new_job_id
                            
                        elif job['job_type'] == 'tts':
                            story_path = Path(params['story_path'])
                            thread = threading.Thread(target=run_tts_worker, args=(new_job_id, story_path))
                            thread.daemon = True
                            thread.start()
                            st.session_state['current_tts_job_id'] = new_job_id
                            
                        elif job['job_type'] == 'clean':
                            story_path = Path(params['story_path'])
                            thread = threading.Thread(target=run_clean_worker, args=(new_job_id, story_path))
                            thread.daemon = True
                            thread.start()
                            st.session_state['current_clean_job_id'] = new_job_id
                            
                        elif job['job_type'] == 'summary':
                            story_path = Path(params['story_path'])
                            thread = threading.Thread(target=run_summary_worker, args=(new_job_id, story_path))
                            thread.daemon = True
                            thread.start()
                            
                        delete_job(job_id)
                        st.success(f"✅ Retrying job as {new_job_id}")
                        time.sleep(2)
                        st.rerun()
                
                with col2:
                    if st.button(f"Clear Failed Job", key=f"clear_err_{job_id}"):
                        delete_job(job_id)
                        st.rerun()
                return
    
    stories = get_all_stories()
    if not stories:
        st.warning("No stories found.")
        return
    
    story_opts = [str(s.relative_to(Path(OUTPUT_DIR).parent)) for s in stories]
    selected = st.selectbox("Select Story to Clean", story_opts)
    
    if st.button("Clean Story", type="primary"):
        selected_file = next(s for s in stories if str(s.relative_to(Path(OUTPUT_DIR).parent)) == selected)
        
        job_id = f"clean_{int(time.time())}"
        
        thread = threading.Thread(
            target=run_clean_worker,
            args=(job_id, selected_file)
        )
        thread.daemon = True
        thread.start()
        
        st.session_state['current_clean_job_id'] = job_id
        st.success(f"✅ Cleaning started in background! Job ID: {job_id}")
        st.rerun()

if __name__ == "__main__":
    main()