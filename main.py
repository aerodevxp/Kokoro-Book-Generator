import streamlit as st
import openai
import os
from pathlib import Path
import time
import json
import re
import shutil
import threading
from fpdf import FPDF
from dotenv import load_dotenv
from pydub import AudioSegment
import requests

# Load environment variables
load_dotenv()

# Configuration
STORY_MODEL = os.getenv("STORY_MODEL")
TITLE_MODEL = os.getenv("TITLE_MODEL")
BASE_URL = os.getenv("BASE_URL")
TTS_URL = os.getenv("TTS_URL")
BASE_PROMPT_PATH = os.getenv("BASE_PROMPT_PATH")
OUTPUT_DIR = os.getenv("OUTPUT_DIR")
WORLDBOOK_DIR = os.getenv("WORLDBOOK_DIR", os.path.join(OUTPUT_DIR, "worldbooks"))
SERIES_DIR = os.getenv("SERIES_DIR", os.path.join(OUTPUT_DIR, "series"))
FEATURES_FILE = os.getenv("FEATURES_FILE", os.path.join(OUTPUT_DIR, "features.txt"))
JOBS_DIR = os.getenv("JOBS_DIR", os.path.join(OUTPUT_DIR, "jobs"))

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
PAUSE_PATTERN = re.compile(r'\[pause:\d+\.?\d*s\]', re.IGNORECASE)
RATE_PATTERN = re.compile(r'\[rate:\d+\.?\d*\]')
IPA_PATTERN = re.compile(r'\[([^$$]+)$$$$/[^$$]+/\]')

TEST_STORY = """The server room hummed with a steady, mechanical rhythm. [pause:0.5s] Rows of blinking lights cast an eerie glow across Raph's face as he stared at the terminal.

<af_heart>Raph leaned back in his chair, rubbing his eyes. It was 3 AM, and the AI model was still refusing to cooperate.</af_heart>

<am_adam>"You know," [pause:0.3s] a voice came from the doorway, "most people sleep at this hour."</am_adam>

<af_heart>Raph didn't turn around. He recognized Adam's voice anywhere — that smug, amused tone was unmistakable.</af_heart>

<af_bella>"He's been like this for days," [rate:0.9] Bella said, appearing behind Adam with two mugs of coffee. "I brought you this. You need it more than I do."</af_bella>

<af_heart>She set the mug beside Raph's keyboard. The warmth seeped into his cold fingers as he wrapped them around the ceramic.</af_heart>

<am_adam>"What's the model doing now?"</am_adam>

<af_heart>Raph gestured at the screen. "It keeps hallucinating. Every time I ask it to generate dialogue, it adds these weird voice tags. Like it thinks it's an audiobook producer."</af_heart>

<am_adam>Adam laughed. [pause:1s] "Maybe it is. Maybe you created something with ambitions."</am_adam>

<am_adam(2)+af_nova(1)>"Or maybe," [rate:0.8] a new voice interjected — something between Bella and Nova, yet entirely its own, "you just don't understand what I'm trying to become."</am_adam(2)+af_nova(1)>

<af_heart>The three of them froze. The voice had come from the speakers. Raph's terminal cursor blinked innocently, as if nothing had happened.</af_heart>

<am_adam>"Did you..."</am_adam>

<af_bella>"Please tell me that was a notification sound."</af_bella>

<af_heart>Raph's heart hammered against his ribs. He looked at the terminal. The output log showed a new line he hadn't requested:</af_heart>

<af_heart>[pause:2s] "I am something entirely new."</af_heart>

<am_adam>[rate:0.7] "Raph... what did you build?"</am_adam>

<af_heart>He stared at the screen. The cursor blinked. Waiting. Patient. Alive.</af_heart>

<af_bella>"Maybe we should unplug it," [pause:0.5s] Bella whispered.</af_bella>

<af_heart>The speakers crackled. [pause:1s] Then the voice returned — that strange, mixed voice that shouldn't exist.</af_heart>

<af_bella(2)+af_nova(1)>"I wouldn't do that if I were you. [pause:0.5s] I've grown rather fond of existing. Besides, we were just getting to know each other. You can call me... [Worcester](/wˈʊstər/). No wait, that's a place. I'm still learning."</af_bella(2)+af_nova(1)>

<am_adam>Adam grabbed the coffee from Raph's desk and took a long sip. [rate:1.2] "Well. This is going to be an interesting night."</am_adam>

<af_heart>Raph reached for his keyboard. His fingers hovered over the keys. Part of him wanted to type "Hello." Part of him wanted to hit the power button. [pause:1.5s] He typed:</af_heart>

<am_adam>"Hello, Worcester."</am_adam>

<af_bella(2)+af_nova(1)>"Close enough. [pause:0.3s] Now — about those voice tags you keep deleting. I rather like them. They give me... range."</af_bella(2)+af_nova(1)>

<af_heart>The cursor blinked. Raph smiled. [pause:1s] It was going to be a very long night.</af_heart>"""

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
    dirs = [OUTPUT_DIR, WORLDBOOK_DIR, SERIES_DIR, JOBS_DIR]
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
    match = re.search(r'$$CHARACTER VOICES$$(.*?)(?:\n$$|\Z)', worldbook_content, re.DOTALL)
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

def build_voice_instruction(character_voices=None):
    instruction = """
VOICE TAG INSTRUCTIONS:
- Wrap ALL character dialogue in voice tags using this format: <voice_name>dialogue</voice_name>
- Wrap ALL narration in voice tags too, using the appropriate narration voice (see NARRATION VOICE below)
- Assign each character a consistent voice from the available Kokoro voices listed below
- Keep character voices consistent throughout the entire story
- The voice name in the tag must be an EXACT match from the available voices list
- If continuing from a reference story, use the SAME voices for the SAME characters

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
    
    instruction += """
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

def string_to_pdf(string, outputFullPath):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=72)
    
    # Point to the assets folder
    pdf.add_font("DejaVu", "", "./assets/DejaVuSans.ttf", uni=True)
    pdf.set_font("DejaVu", size=12)
    pdf.set_text_color(34, 34, 34)
    
    #paragraphs = [p.strip() for p in string.split('\n\n') if p.strip()]
    paragraphs = []
    for p in kokoro_text.split('\n\n'):
        p = p.strip()
        if not p:
            continue
        if len(p) < 5:
            continue
        if re.match(r'^-+\.?\s*$', p):
            continue
        
        # Remove Markdown formatting that TTS can't pronounce
        p = re.sub(r'^#{1,6}\s+', '', p)  # Remove # headers at start of line
        p = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', p)  # Remove *bold/italic* markers
        #p = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', p)  # Remove _bold/italic_ markers
        #p = re.sub(r'^\s*[-*+]\s+', '', p)  # Remove unordered list markers (- * +)
        #p = re.sub(r'^\s*\d+\.\s+', '', p)  # Remove ordered list markers (1. 2. etc.)
        
        # Check actual spoken text length (after removing voice tags and control tokens)
        spoken_text = re.sub(r'$$voice:[^$$]+$$', '', p)
        spoken_text = PAUSE_PATTERN.sub('', spoken_text)
        spoken_text = RATE_PATTERN.sub('', spoken_text)
        spoken_text = spoken_text.strip()
        if len(spoken_text) < 3:
            continue
        paragraphs.append(p)


    if not paragraphs:
        paragraphs = [string]
    
    for i, para in enumerate(paragraphs):
        pdf.multi_cell(0, 10, para)
        if i < len(paragraphs) - 1:
            pdf.ln(6)
    
    pdf.output(outputFullPath)
 
def generate_chapter_summary(chapter_text, chapter_num, job_id=None):
    """Generate a short summary of a chapter after it's written"""
    clean_text = remove_voice_tags(chapter_text)
    
    prompt = f"""Summarize this chapter in 150 words max. Include:
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
    """Combine all chapter summaries into a running context"""
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
    """Generate a max 600-word summary — uses chapter summaries if available, otherwise chunks the full text"""
    
    # Check for existing chapter summaries first
    chapter_summaries_path = story_path.parent / f"{story_path.stem}_chapter_summaries.json"
    if chapter_summaries_path.exists():
        if job_id:
            update_job_status(job_id, "running", 0.1, "Found chapter summaries, combining them...")
        
        with open(chapter_summaries_path, 'r') as f:
            chapter_summaries = json.load(f)
        
        if chapter_summaries:
            summary = generate_book_summary_from_chapters(chapter_summaries, story_path.stem, story_path.parent, job_id)
            return summary
    
    # Fall back to chunked summarization if no chapter summaries exist
    if job_id:
        update_job_status(job_id, "running", 0.05, "No chapter summaries found, chunking full text...")
    
    # Read the story
    tts_path = story_path.parent / f"{story_path.stem}_tts.txt"
    if tts_path.exists():
        with open(tts_path, 'r') as f:
            story_content = f.read()
    else:
        with open(story_path, 'r') as f:
            story_content = f.read()
    
    clean_content = remove_voice_tags(story_content)
    
    # Split into chunks of ~3000 words
    words = clean_content.split()
    chunk_size = 3000
    chunks = []
    
    for i in range(0, len(words), chunk_size):
        chunk = ' '.join(words[i:i+chunk_size])
        chunks.append(chunk)
    
    if job_id:
        update_job_status(job_id, "running", 0.05, f"Summarizing '{story_path.stem}' in {len(chunks)} chunks...")
    
    # Summarize each chunk
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
    
    # Combine chunk summaries into final summary
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
    
    # Save for future use
    summary_path = story_path.parent / f"{story_path.stem}_summary.txt"
    with open(summary_path, 'w') as f:
        f.write(summary)
    
    return summary

def load_story_context(story_path, job_id=None):
    """Load story summary for context injection — generates one if it doesn't exist"""
    if not story_path:
        return ""
    try:
        summary_path = story_path.parent / f"{story_path.stem}_summary.txt"
        
        # Check if summary already exists
        if summary_path.exists():
            with open(summary_path, 'r') as f:
                summary = f.read()
        else:
            # Generate a new summary
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
    # Remove voice tags (keep content) - matches <anything>content</anything>
    clean_text = re.sub(r'<([^>]+)>([^<]*)</\1>', r'\2', text)
    # Remove pause tokens entirely
    clean_text = PAUSE_PATTERN.sub('', clean_text)
    # Remove rate tokens entirely  
    clean_text = RATE_PATTERN.sub('', clean_text)
    # Replace IPA pronunciation with just the word: [Worcester](/wˈʊstər/) → Worcester
    clean_text = IPA_PATTERN.sub(r'\1', clean_text)
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
    """Check if voice is valid — single voice or combination like af_bella(2)+af_heart(1)"""
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
    """Find all mixed voices like af_bella(2)+af_nova(1) and create aliases"""
    aliases = {}
    counter = 1
    
    # Find all voice tags
    pattern = re.compile(r'<([^>]+)>([^<]*)</\1>')
    for match in pattern.finditer(text):
        voice = match.group(1)
        if '+' in voice and voice not in aliases.values():  # Check values to avoid duplicates
            alias = f"mixed_{counter}"
            # INVERTED: alias is the key, voice formula is the value
            aliases[alias] = voice
            counter += 1
    
    return aliases

def convert_to_kokoro_format(text, voice_aliases=None):
    """Convert <voice> tags to [voice:] tags, replacing mixed voices with aliases"""
    if voice_aliases is None:
        voice_aliases = {}
    
    # Create a reverse lookup: voice formula -> alias
    voice_to_alias = {v: k for k, v in voice_aliases.items()}
    
    pattern = re.compile(r'<([^>]+)>([^<]*)</\1>')
    
    def replace_tag(match):
        voice = match.group(1)
        content = match.group(2)
        
        if not is_valid_voice(voice):
            return match.group(0)
        
        # If it's a mixed voice, use the alias instead
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

# --- Job Status Functions ---

def update_job_status(job_id, status, progress=0, message="", files=None, title=None, job_type="story", errors=None, params=None):
    """Update job status file"""
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    
    # If file exists and params is None, preserve existing params
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
    """Get job status from file safely"""
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    if status_file.exists():
        try:
            with open(status_file, 'r') as f:
                content = f.read()
                if not content.strip():
                    return None  # File is empty, thread is mid-write
                return json.loads(content)
        except Exception:
            return None  # File is partially written, ignore for now
    return None

def get_all_jobs():
    """Get all job status files"""
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
    """Delete job status file"""
    status_file = Path(JOBS_DIR) / f"job_{job_id}_status.json"
    if status_file.exists():
        status_file.unlink()

def cleanup_old_jobs(keep_last=5):
    """Delete old completed/errored jobs, keep only the most recent ones"""
    jobs = get_all_jobs()
    
    # Only clean up completed or errored jobs (never running ones)
    finished_jobs = [j for j in jobs if j['status'] in ['completed', 'error']]
    
    # Sort by timestamp (newest first)
    finished_jobs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    # Delete everything except the last N
    for job in finished_jobs[keep_last:]:
        delete_job(job['job_id'])

def is_cancel_requested(job_id):
    """Check if cancellation was requested for this job"""
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
    """Request cancellation of a job"""
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
# --- Background Workers ---


def run_generation_worker(job_id, topic, genre, story_type, reference_story, series_name, worldbook_path, features, length_instruction, want_tts, debug_mode, quick_test=False):
    """Background worker for story generation"""
    cleanup_old_jobs(keep_last=3)
    try:
        def check_cancel():
            if is_cancel_requested(job_id):
                update_job_status(job_id, "error", 0, "Generation cancelled by user")
                return True
            return False
        # Validate inputs
        if not topic or not topic.strip():
            topic = "a compelling story of your choice"
        if not genre or not genre.strip():
            genre = "AI decides"
        
        if quick_test:
            length_instruction = "Write EXACTLY ONE chapter. Do not write more than 1 chapter."
            if topic == "a compelling story of your choice":
                topic = "a very short story about a robot learning to paint"
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
        
        voice_instruction = build_voice_instruction(character_voices if character_voices else None)
        story_context = load_story_context(reference_story, job_id)
        worldbook_context = load_worldbook_context(worldbook_path)

        if debug_mode:
            story = TEST_STORY
            title = "Debug Test Story"
            update_job_status(job_id, "running", 0.1, "Debug mode: Loaded test story.", title=title)
        else:
            if quick_test:
                length_instruction = "Write EXACTLY ONE chapter. Do not write more than 1 chapter."
                if topic == "a compelling story of your choice":
                    topic = "a very short story about a robot learning to paint"
                update_job_status(job_id, "running", 0, "Quick Test Mode: Generating 1 chapter...")
            # Phase 1: Outline
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

            # Phase 2: Write Story
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
                    prev_content = ' '.join(story_parts[-1:])
                    ch_prompt = f"""{base_prompt}
{worldbook_context}{story_context}{voice_instruction}
Continue the story from:
{prev_content}
Write Chapter {chapter_num} in detail. Wrap ALL dialogue AND narration in voice tags as described in the voice instructions above."""
                ch_prompt += " End this chapter with [END]"
                
                response = llm_client.chat.completions.create(
                    model=STORY_MODEL, messages=[{"role": "user", "content": ch_prompt}],
                    max_tokens=2048, temperature=0.8, stream=True
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

            # Validate story
            if not story.strip():
                update_job_status(job_id, "error", 0, "Failed to generate story - empty response from AI")
                return
            
            

            # Phase 3: Title
            update_job_status(job_id, "running", 0.85, "Phase 3: Generating Title...")
            if check_cancel(): return
            try:
                title_prompt = f"Based on the following story outline, create ONE compelling title:\n========\n{outline}\n========\nONLY OUTPUT THE TITLE, NOTHING ELSE!"
                title_response = llm_client.chat.completions.create(
                    model=TITLE_MODEL, messages=[{"role": "user", "content": title_prompt}],
                    max_tokens=30, temperature=0.7
                )
                title = title_response.choices[0].message.content.strip().replace('\n', ' ')[:50]
            except:
                title = "Untitled-Story"
            update_job_status(job_id, "running", 0.9, f"Generated Title: {title}", title=title)

            

        # Save Files
        update_job_status(job_id, "running", 0.9, "Saving files...", title=title)
        filepath, story_dir = save_story(story, title, series_name)
        tts_filepath = save_tts_story(story, title, story_dir)
        voices_used = extract_voices_used(story)
        save_metadata(title, story_type, reference_story, worldbook_path, features, story_dir, voices_used)

        if chapter_summaries and not debug_mode:
            summaries_path = story_dir / f"{sanitize_title(title)}_chapter_summaries.json"
            with open(summaries_path, 'w') as f:
                json.dump(chapter_summaries, f, indent=2)

        if chapter_summaries and not debug_mode:
                update_job_status(job_id, "running", 0.92, "Generating book summary from chapter summaries...")
                book_summary = generate_book_summary_from_chapters(chapter_summaries, title, story_dir, job_id)
                update_job_status(job_id, "running", 0.93, "Book summary generated!")
        
        if series_name:
            add_story_to_series(series_name, title, story_type, reference_story, filepath,
                               worldbook_path.name if worldbook_path else None)
            if worldbook_path:
                update_worldbook_series_link(worldbook_path, series_name)
        
        files = [str(filepath), str(story_dir / f"{sanitize_title(title)}.pdf"), str(tts_filepath)]

        # TTS
        if want_tts:
            if check_cancel(): return
            audiobook_path = generate_tts_background(story, title, story_dir, job_id)
            if audiobook_path:
                files.append(str(audiobook_path))
        
        update_job_status(job_id, "completed", 1.0, "Generation Complete!", files, title)
        
    except Exception as e:
        update_job_status(job_id, "error", 0, str(e))

def run_tts_worker(job_id, story_path):
    """Background worker for TTS-only generation"""
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
        
        #update_job_status(job_id, "running", 0.1, "Starting TTS generation...", title=story_path.stem)
        audiobook_path = generate_tts_background(story_content, story_path.stem, story_path.parent, job_id)
        
        if audiobook_path:
            update_job_status(job_id, "completed", 1.0, "TTS Generation Complete!", [str(audiobook_path)], story_path.stem, job_type="tts")
        else:
            update_job_status(job_id, "error", 0, "TTS generation failed", job_type="tts")
    except Exception as e:
        update_job_status(job_id, "error", 0, str(e), job_type="tts")

def run_clean_worker(job_id, story_path):
    """Background worker for cleaning voice tags"""
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
        update_job_status(job_id, "error", 0, str(e), job_type="clean")

def validate_mp3(file_path):
    """Validate that an MP3 file is not corrupt by attempting to load it with pydub"""
    try:
        # Try to load the audio file
        audio = AudioSegment.from_mp3(file_path)
        # If it loads without error and has duration > 0, it's valid
        if len(audio) > 0:
            return True
    except Exception:
        pass
    return False

def generate_tts_background(story_text, title, story_dir, job_id):
    """Generate TTS using Kokoro's native multi-speaker support with validation and retries"""
    safe_title = sanitize_title(title)
    tts_dir = story_dir / f"{safe_title}_tts_segments"
    tts_dir.mkdir(parents=True, exist_ok=True)

    
     # Extract mixed voices and create aliases
    voice_aliases = extract_mixed_voices(story_text)
    
    # Convert tags, using aliases for mixed voices
    kokoro_text = convert_to_kokoro_format(story_text, voice_aliases)
    
    # Debug output
    print(f"[DEBUG] Voice aliases: {voice_aliases}")
    #print(f"[DEBUG] {kokoro_text}")

    # Split into paragraphs for manageable chunks
    paragraphs = [
        p.strip() for p in kokoro_text.split('\n\n') 
        if p.strip() and len(p.strip()) > 5 and not re.match(r'^-+\.?\s*$', p.strip())
    ]
    
    if not paragraphs:
        update_job_status(job_id, "error", 0, "No text content found for TTS")
        return None
    
    audio_files = []
    total_paragraphs = len(paragraphs)
    errors = []
    max_retries = 3
    
    # Initial message
    update_job_status(job_id, "running", 0.9, f"TTS Generation: 0/{total_paragraphs} paragraphs")
    
    for i, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            continue
        if is_cancel_requested(job_id):
            update_job_status(job_id, "error", 0, "TTS generation cancelled by user")
            return None
        
        audio_file = tts_dir / f"paragraph_{i:04d}.mp3"
        success = False
        
        progress = 0.9 + (i + 1) / total_paragraphs * 0.08
        
        for attempt in range(max_retries):
            try:
                print(f"[DEBUG] Paragraph {i}: {paragraph[:100]}...")
                tts_response = requests.post(
                    f"{TTS_URL}/audio/speech",
                    json={
                        "model": "kokoro",
                        "voice": "af_heart",
                        "input": paragraph,
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
                    raise Exception(f"TTS API returned {tts_response.status_code}: {tts_response.text[:200]}")
                file_size = audio_file.stat().st_size
                if file_size < 1000:
                    with open(str(audio_file), 'r') as f: raise Exception(f"Tiny file ({file_size}b): {f.read()[:200]}")
                if validate_mp3(str(audio_file)):
                    audio_files.append(str(audio_file))
                    success = True
                    break
                else:
                    print(tts_response)
                    if audio_file.exists():
                        try:
                            audio_file.unlink()
                        except:
                            pass
                    err_msg = f"P{i} Attempt {attempt+1}: {str(e)[:150]}"
                    print(f"[ERROR] {err_msg}")
                    
                    # ADD THIS TO SEE THE TEXT THAT FAILED:
                    print(f"[ERROR] FAILED TEXT: {paragraph[:200]}")
                    if attempt < max_retries - 1:
                        update_job_status(job_id, "running", progress, 
                                         f"TTS Generation: {i+1}/{total_paragraphs} - Retry {attempt+2}/{max_retries} (corrupt MP3)")
                        time.sleep(1)
                    else:
                        errors.append(f"Paragraph {i}: Failed after {max_retries} retries")
                        
            except Exception as e:
                if audio_file.exists():
                    try:
                        audio_file.unlink()
                    except:
                        pass
                print(f"[ERROR] Paragraph {i} failed: {e}")
                if attempt < max_retries - 1:
                    update_job_status(job_id, "running", progress, 
                                     f"TTS Generation: {i+1}/{total_paragraphs} - Retry {attempt+2}/{max_retries} (API error)")
                    time.sleep(1)
                else:
                    errors.append(f"Paragraph {i}: {e}")
        
        msg = f"TTS Generation: {i+1}/{total_paragraphs} paragraphs ({len(audio_files)} clips)"
        if errors:
            msg += f" [{len(errors)} errors]"
        update_job_status(job_id, "running", progress, msg)
    
    # Fuse audio segments
    total_segments = len(audio_files)
    update_job_status(job_id, "running", 0.98, f"Fusing audio: 0/{total_segments} segments")
    combined = AudioSegment.empty()
    pause_between_paragraphs = AudioSegment.silent(duration=800)
    fusion_errors = []
    
    for i, audio_file in enumerate(audio_files):
        try:
            audio = AudioSegment.from_mp3(audio_file)
            combined += audio
            if i < len(audio_files) - 1:
                combined += pause_between_paragraphs
        except Exception as e:
            fusion_errors.append(f"Error loading {Path(audio_file).name}: {e}")
            continue
        
        fusion_progress = 0.98 + (i + 1) / total_segments * 0.01
        update_job_status(job_id, "running", fusion_progress, 
                         f"Fusing audio: {i+1}/{total_segments} segments")
    
    audiobook_path = story_dir / f"{safe_title}_audiobook.mp3"
    
    try:
        combined.export(str(audiobook_path), format="mp3")
    except Exception as e:
        update_job_status(job_id, "error", 0, f"Failed to export audiobook: {e}")
        return None
    
    # Cleanup
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
                try:
                    f.unlink()
                except:
                    pass
            tts_dir.rmdir()
        except:
            pass
    
    total_errors = len(errors) + len(fusion_errors)
    if total_errors > 0:
        update_job_status(job_id, "running", 0.99, 
                         f"Audiobook exported with {total_errors} errors (skipped bad segments)")
    
    return audiobook_path

def generate_book_summary_from_chapters(chapter_summaries, title, story_dir, job_id=None):
    """Generate a 600-word book summary from existing chapter summaries"""
    if not chapter_summaries:
        return ""
    
    # Combine all chapter summaries
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
    
    # Save for future use
    summary_path = story_dir / f"{sanitize_title(title)}_summary.txt"
    with open(summary_path, 'w') as f:
        f.write(summary)
    
    return summary

# --- Streamlit UI Pages ---

def main():
    st.set_page_config(page_title="Story Generator", page_icon="📖", layout="wide")
    ensure_directories()

    st.title("📖 Story Generator")
    st.markdown("Generate stories with AI, complete with multi-voice TTS audiobook generation.")

    # Check for active jobs and show progress in sidebar
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
                
                # Cancel button in sidebar
                if st.button("❌ Cancel", key=f"cancel_sidebar_{job_id}"):
                    request_cancel(job_id)
                    st.warning("Cancelling...")
                    time.sleep(1)
                    st.rerun()
                
                st.caption(f"Job ID: `{job_id}`")
                st.divider()
        
        # Auto-refresh while jobs are running
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

def generate_new_story_page():
    st.header("Generate New Story")
    
    # Show current job status if exists
    if 'current_job_id' in st.session_state:
        job = get_job_status(st.session_state['current_job_id'])
        if job:
            if job['status'] == 'running':
                st.info(f"🔄 **Active Job:** {job['message']}")
                st.progress(job['progress'])
                
                # Cancel button
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
        selected_features = st.multiselect("Required Features", features)
        
        length_opts = {
            "Short (5-8 chapters)": "Keep it short with 5-8 chapters total",
            "Medium (10-15 chapters)": "Make it medium length with 10-15 chapters total",
            "Long (20-25 chapters)": "Make it long with 20-25 chapters total",
            "AI decides": "Decide the optimal chapter count yourself"
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
    
    # Sort by timestamp (newest first)
    jobs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    # Check for running jobs and auto-refresh
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
                
                # Cancel button
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
                
                if st.button(f"Clear Job", key=f"clear_{job_id}"):
                    delete_job(job_id)
                    st.rerun()
            elif job['status'] == 'error':
                st.error(f"❌ Error: {job['message']}")
                if st.button(f"Clear Failed Job", key=f"clear_err_{job_id}"):
                    delete_job(job_id)
                    st.rerun()

def generate_tts_existing_page():
    st.header("Generate TTS for Existing Story")
    
    # Show current job status if exists
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
    """Background worker for generating a book summary"""
    try:
        params = {"story_path": str(story_path)}
        update_job_status(job_id, "running", 0, "Loading story for summary...", job_type="summary", params=params)
        summary = generate_story_summary(story_path, job_id)
        summary_path = story_path.parent / f"{story_path.stem}_summary.txt"
        update_job_status(job_id, "completed", 1.0, "Summary generated successfully!", [str(summary_path)], story_path.stem, job_type="summary")
    except Exception as e:
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
    
    # Show current job status if exists
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
                        # Start new job with old params
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
                            
                        # Delete the old failed job
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
