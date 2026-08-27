import argparse
import re
import openai
import os
from pathlib import Path
import time
import requests
import sys
import json
import shutil
from spire.doc import *
from dotenv import load_dotenv
from pydub import AudioSegment

# Load environment variables from .env file
load_dotenv()

# Configuration from environment variables
STORY_MODEL = os.getenv("STORY_MODEL")
TITLE_MODEL = os.getenv("TITLE_MODEL")
BASE_URL = os.getenv("BASE_URL")
TTS_URL = os.getenv("TTS_URL")
BASE_PROMPT_PATH = os.getenv("BASE_PROMPT_PATH")
OUTPUT_DIR = os.getenv("OUTPUT_DIR")
WORLDBOOK_DIR = os.getenv("WORLDBOOK_DIR", os.path.join(OUTPUT_DIR, "worldbooks"))
SERIES_DIR = os.getenv("SERIES_DIR", os.path.join(OUTPUT_DIR, "series"))
FEATURES_FILE = os.getenv("FEATURES_FILE", os.path.join(OUTPUT_DIR, "features.txt"))

# Valid Kokoro voices
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

# Precompiled regex patterns
VOICE_PATTERN = re.compile(r"<(am|af|bm|bf|ef|em|ff|hf|hm|if|im|jf|jm|pf|pm|zf|zm)[^>]*>([^<]*)</\1[^>]*>")
QUOTE_PATTERN = re.compile(r'(["""])([^"""]*)\1')

# Test story for debugging
TEST_STORY = """The coffee shop was quiet that afternoon. Rain pattered against the windows, creating a cozy atmosphere inside.

<af_bella>Can you believe it's been three years?</af_bella> Bella said, stirring her latte.

<am_adam>Time flies, doesn't it?</am_adam> Raph replied, leaning back in his chair. <am_adam>Feels like yesterday we were all in college together.</am_adam>

<af_nova>I miss those days.</af_nova> Nova sighed, looking out the window. <af_nova>Everything was so much simpler.</af_nova>

Bella nodded. <af_bella>Simpler, maybe. But I wouldn't trade where we are now for anything.</af_bella>

Raph smiled. <am_adam>To the future, then.</am_adam>

<af_nova>To the future.</af_nova>"""

# Initialize OpenAI clients
llm_client = openai.OpenAI(base_url=BASE_URL, api_key=os.getenv("LLM_API_KEY", "dummy-key"))
tts_client = openai.OpenAI(base_url=TTS_URL, api_key=os.getenv("TTS_API_KEY", "not-needed"))

def show_help():
    """Display help information"""
    help_text = """
=== STORY GENERATOR HELP ===

This script generates stories with the following features:
- Customizable story parameters (topic, genre, length, features)
- Series management (sequels/prequels) with dedicated folders
- Worldbook integration (contextual world information)
- Multi-voice TTS generation using Kokoro voices
- Automatic audio fusion into single MP3 audiobook
- Metadata tracking

File Structure:
/books/
  ├── StoryTitle/                # Dedicated folder for each story
  │   ├── StoryTitle.txt         # Main story (NO voice tags)
  │   ├── StoryTitle.pdf         # PDF version
  │   ├── StoryTitle_tts.txt     # Tagged version (WITH voice tags for TTS)
  │   ├── StoryTitle_audiobook.mp3 # Combined TTS audio file
  │   └── StoryTitle_metadata.json
  ├── worldbooks/                # World context files (.txt)
  ├── series/                    # Organized story series
  │   └── SERIES_NAME/
  │       └── StoryTitle/        # Story folder inside series
  └── features.txt               # Available story features

Commands:
  --show-help         Show this help
  --tts-only          Generate TTS for existing story file
  --create-worldbook  Create new worldbook interactively
  --clean-story       Remove voice tags from existing story file
  --debug             Use test story for debugging (no AI generation)
"""
    print(help_text)

def read_base_prompt():
    """Read the base prompt from file"""
    try:
        with open(BASE_PROMPT_PATH, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def ensure_directories():
    """Ensure all required directories exist"""
    dirs = [OUTPUT_DIR, WORLDBOOK_DIR, SERIES_DIR]
    for directory in dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)

def show_spinner(label):
    """Show a simple spinner for ongoing processes"""
    spinner = "|/-\\"
    return f"{label} {spinner[show_spinner.counter % 4]}"
show_spinner.counter = 0

def string_to_pdf(string, outputFullPath):
    """Convert string to PDF"""
    document = Document()
    section = document.AddSection()
    section.PageSetup.Margins.All = 72
    p = section.AddParagraph()
    text_range = p.AppendText(string)
    text_range.CharacterFormat.FontName = "Arial"
    text_range.CharacterFormat.FontSize = 21
    text_range.CharacterFormat.TextColor = Color.FromRgb(34, 34, 34)
    document.SaveToFile(outputFullPath, FileFormat.PDF)
    document.Close()

def stream_with_spinner(response, label):
    """Stream response with spinner indicator"""
    full_content = ""
    token_count = 0
    show_spinner.counter = 0
    print(f"{label}...", end="", flush=True)
    try:
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta:
                content = chunk.choices[0].delta.content
                if content:
                    full_content += content
                    token_count += 1
                    if token_count % 15 == 0:
                        show_spinner.counter += 1
                        sys.stdout.write(f"\r{show_spinner(label)} ({token_count} tokens)")
                        sys.stdout.flush()
        print(f"\r{label}... Done! ({token_count} tokens)")
        return full_content
    except Exception as e:
        print(f"\nError during streaming: {e}")
        raise

def build_voice_instruction():
    """Build voice tag instruction for the AI"""
    return """
VOICE TAG INSTRUCTIONS:
- Wrap ALL character dialogue in voice tags using this format: <voice_name>dialogue</voice_name>
- Do NOT wrap narration in voice tags - narration uses the default voice (af_heart)
- Assign each character a consistent voice from the available Kokoro voices listed below
- Keep character voices consistent throughout the entire story
- The voice name in the tag must be an EXACT match from the available voices list
- If continuing from a reference story, use the SAME voices for the SAME characters

Available voices:
Female: af_heart, af_alloy, af_aoede, af_bella, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky, bf_alice, bf_emma, bf_isabella, bf_lily, jf_alpha, jf_gongitsune, jf_nezumi, jf_tebukuro, zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, ef_dora, ff_siwis, hf_alpha, hf_beta, if_sara, pf_dora
Male: am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx, am_puck, am_santa, bm_daniel, bm_fable, bm_george, bm_lewis, jm_kumo, zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang, em_alex, em_santa, hm_omega, hm_psi, im_nicola, pm_alex, pm_santa

Example:
<af_bella>"I can't believe you did that!"</af_bella>
<am_adam>"It was the only way."</am_adam>
"""

def extract_voices_used(story):
    """Extract unique voice names from tagged story"""
    voices = set()
    for match in VOICE_PATTERN.finditer(story):
        voice_tag = match.group(0)
        voice = voice_tag[1:voice_tag.find('>')]
        voices.add(voice)
    return list(voices)

def load_features():
    """Load available features from file"""
    features = []
    try:
        with open(FEATURES_FILE, 'r') as f:
            features = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Features file not found: {FEATURES_FILE}")
        default_features = [
            "Magic System", "Political Intrigue", "Romantic Subplot",
            "Mystery Element", "Action Sequences", "Character Development",
            "Philosophical Themes", "Supernatural Elements",
            "Technology Integration", "Survival Elements"
        ]
        with open(FEATURES_FILE, 'w') as f:
            f.write('\n'.join(default_features))
        features = default_features
        print(f"Created default features file: {FEATURES_FILE}")
    return features

def get_user_input():
    """Get story topic and genre from user"""
    print("=== STORY GENERATOR ===")
    topic = input("What should the story be about? (leave blank for AI to decide): ").strip()
    if not topic:
        topic = "a compelling story of your choice"
    genre = input("What genre should the story be? (leave blank for AI to decide): ").strip()
    if not genre:
        genre = "AI decides"
    print(f"Selected genre: {genre}")
    return topic, genre

def get_story_type():
    """Determine if story is standalone, sequel, or prequel"""
    print("\nStory type options:")
    print("1. Standalone story")
    print("2. Sequel to existing story")
    print("3. Prequel to existing story")
    choice = input("Choose story type (1-3, default=1): ").strip()
    if choice == "2":
        return "sequel"
    elif choice == "3":
        return "prequel"
    else:
        return "standalone"

def select_reference_story():
    """Allow user to select an existing story as reference"""
    story_files = []
    # Check main directory subfolders
    for story_dir in Path(OUTPUT_DIR).iterdir():
        if story_dir.is_dir():
            story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    # Check series directories
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            for story_dir in series_dir.iterdir():
                if story_dir.is_dir():
                    story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    
    if not story_files:
        print("No existing stories found.")
        return None
    
    print("\nAvailable stories:")
    for i, story in enumerate(story_files, 1):
        try:
            rel_path = story.relative_to(Path(OUTPUT_DIR).parent)
        except:
            rel_path = story
        print(f"{i}. {rel_path}")
    
    try:
        choice = input("Select reference story (0 for none): ").strip()
        if choice == "0" or not choice:
            return None
        index = int(choice) - 1
        if 0 <= index < len(story_files):
            return story_files[index]
    except (ValueError, IndexError):
        print("Invalid selection, proceeding without reference story")
    return None

def load_story_context(story_path):
    """Load story content for context injection - prefers _tts.txt version for voice consistency"""
    if not story_path:
        return ""
    try:
        tts_path = story_path.parent / f"{story_path.stem}_tts.txt"
        if tts_path.exists():
            with open(tts_path, 'r') as f:
                content = f.read()
        else:
            with open(story_path, 'r') as f:
                content = f.read()
        return f"Reference Story Context (from '{story_path.stem}'):\n{content}\n\n"
    except Exception as e:
        print(f"Error loading reference story: {e}")
        return ""

def select_worldbook():
    """Allow user to select a worldbook for context"""
    worldbooks = list(Path(WORLDBOOK_DIR).glob("*.txt"))
    if not worldbooks:
        print("No worldbooks found.")
        return None
    print("\nAvailable worldbooks:")
    for i, wb in enumerate(worldbooks, 1):
        print(f"{i}. {wb.stem}")
    try:
        choice = input("Select worldbook (0 for none): ").strip()
        if choice == "0" or not choice:
            return None
        index = int(choice) - 1
        if 0 <= index < len(worldbooks):
            return worldbooks[index]
    except (ValueError, IndexError):
        print("Invalid selection, proceeding without worldbook")
    return None

def load_worldbook_context(worldbook_path):
    """Load worldbook content for context injection"""
    if not worldbook_path:
        return ""
    try:
        with open(worldbook_path, 'r') as f:
            content = f.read()
            return f"World Context (from '{worldbook_path.stem}'):\n{content}\n\n"
    except Exception as e:
        print(f"Error loading worldbook: {e}")
        return ""

def get_required_features():
    """Get required story features from user"""
    features = load_features()
    print("\nRequired story features (select multiple, comma-separated):")
    for i, feature in enumerate(features, 1):
        print(f"{i}. {feature}")
    print(f"{len(features)+1}. None (AI chooses)")
    feature_choices = input(f"Select features (1-{len(features)+1}, comma-separated): ").strip()
    selected_features = []
    if feature_choices and feature_choices != str(len(features)+1):
        try:
            indices = [int(x.strip()) for x in feature_choices.split(',')]
            selected_features = [features[i-1] for i in indices if 1 <= i <= len(features)]
        except ValueError:
            print("Invalid input, using no specific features")
    print(f"Selected features: {selected_features if selected_features else 'None'}")
    return selected_features

def get_story_length():
    """Get story length from user"""
    print("\nStory length options:")
    print("1. Short (5-8 chapters)")
    print("2. Medium (10-15 chapters)")
    print("3. Long (20-25 chapters)")
    print("4. AI decides")
    length_choice = input("Choose length (1-4, default=4): ").strip()
    length_prompts = {
        "1": "Keep it short with 5-8 chapters total",
        "2": "Make it medium length with 10-15 chapters total",
        "3": "Make it long with 20-25 chapters total",
        "4": "Decide the optimal chapter count yourself",
    }
    length_instruction = length_prompts.get(length_choice, length_prompts["4"])
    print(f"Selected: {length_instruction}")
    return length_instruction

def get_tts_preference():
    """Ask user if they want TTS generation"""
    choice = input("\nGenerate TTS for this story? (Y/n, default=Y): ").strip().lower()
    if choice in ["n", "no"]:
        return False
    return True

def generate_outline(topic, genre, features, worldbook_context, story_context, length_instruction, story_type, voice_instruction):
    """Generate story outline with streaming"""
    base_prompt = read_base_prompt()
    features_instruction = ""
    if features:
        features_list = ", ".join(features)
        features_instruction = f"The story MUST include these elements: {features_list}. "
    type_instruction = ""
    if story_type == "sequel":
        type_instruction = "This is a SEQUEL - continue the story logically from previous events while introducing new conflicts."
    elif story_type == "prequel":
        type_instruction = "This is a PREQUEL - explore events leading up to referenced story with established characters/settings."
    prompt = f"""{base_prompt}

{worldbook_context}{story_context}{voice_instruction}
Generate a detailed story outline.
Topic: {topic}
Genre: {genre}
{type_instruction}
{features_instruction}
Length requirement: {length_instruction}
List each chapter with a brief description."""
    print("\n=== PHASE 1: GENERATING OUTLINE ===")
    response = llm_client.chat.completions.create(
        model=STORY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.8,
        stream=True
    )
    return stream_with_spinner(response, "Generating outline")

def extract_chapter_count(outline):
    """Extract actual chapter count from outline"""
    chapter_matches = re.findall(r'(?:Chapter|chapter)\s+(\d+)', outline, re.IGNORECASE)
    if chapter_matches:
        chapter_numbers = [int(x) for x in chapter_matches]
        actual_chapters = max(chapter_numbers)
        print(f"Detected {actual_chapters} chapters in outline")
        return actual_chapters
    print("No chapters detected, defaulting to 10")
    return 10

def write_story(outline, total_chapters, worldbook_context, story_context, voice_instruction):
    """Write the full story chapter by chapter WITH voice tags"""
    print(f"\n=== PHASE 2: WRITING STORY ({total_chapters} CHAPTERS) ===")
    base_prompt = read_base_prompt()
    story_parts = []
    for chapter_num in range(1, total_chapters + 1):
        if chapter_num == 1:
            prompt = f"""{base_prompt}

{worldbook_context}{story_context}{voice_instruction}
Based on this outline:
{outline}

Write Chapter {chapter_num} in detail. Wrap ALL dialogue in voice tags as described in the voice instructions above."""
        else:
            prev_content = ' '.join(story_parts[-1:])
            prompt = f"""{base_prompt}

{worldbook_context}{story_context}{voice_instruction}
Continue the story from:
{prev_content}

Write Chapter {chapter_num} in detail. Wrap ALL dialogue in voice tags as described in the voice instructions above."""
        prompt += " End this chapter with [END]"
        response = llm_client.chat.completions.create(
            model=STORY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.8,
            stream=True
        )
        chapter = stream_with_spinner(response, f"Writing chapter {chapter_num}/{total_chapters}")
        story_parts.append(chapter)
        print(f"Chapter {chapter_num}: Completed\n")
    return "\n\n".join(story_parts)

def extract_title(storyOutline):
    """Extract or generate title from story"""
    print("=== PHASE 3: GENERATING TITLE ===")
    try:
        prompt = f"Based on the following story outline, create ONE compelling title:\n========\n{storyOutline}\n========\nONLY OUTPUT THE TITLE, NOTHING ELSE!"
        response = llm_client.chat.completions.create(
            model=TITLE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
            temperature=0.7
        )
        generated_title = response.choices[0].message.content.strip().replace('\n', ' ')
        final_title = generated_title[:50]
        print(f"Generated title: {final_title}")
        return final_title
    except:
        fallback = "Untitled-Story"
        print(f"Using fallback title: {fallback}")
        return fallback

def sanitize_title(title):
    """Sanitize title for filename"""
    safe_title = re.sub(r'[<>:"/|?\*\x00-\x1F.]', '_', title)[:100]
    safe_title = safe_title.rstrip('_')
    return safe_title or "Untitled-Story"

def save_metadata(title, story_type, reference_story, worldbook_used, features_used, story_dir, voices_used=None):
    """Save metadata for the story"""
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
    print(f"Metadata saved: {metadata_file}")

def remove_voice_tags(text):
    """Remove voice tags from text"""
    clean_text = VOICE_PATTERN.sub(r'\2', text)
    return clean_text

def save_story(story, title, series_name=None):
    """Save story WITHOUT voice tags (main story file + PDF) in a dedicated subfolder"""
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
    print(f"Main story (no voice tags) saved: {filepath}")
    return filepath, story_dir

def save_tts_story(story, title, story_dir):
    """Save the tagged version of the story for TTS generation"""
    safe_title = sanitize_title(title)
    tts_filename = f"{safe_title}_tts.txt"
    tts_filepath = story_dir / tts_filename
    with open(tts_filepath, 'w') as f:
        f.write(story)
    print(f"TTS story (with voice tags) saved: {tts_filepath}")
    return tts_filepath

def split_into_paragraphs(text):
    """Split text into paragraphs for better TTS granularity"""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    return paragraphs

def parse_tts_text(text):
    """Parse text and extract voice segments"""
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

def combine_audio_files(audio_files, output_path):
    """Combine multiple audio files into one MP3"""
    if not audio_files:
        print("No audio files to combine.")
        return False
        
    print(f"\nFusing {len(audio_files)} audio segments into single MP3...")
    
    combined = AudioSegment.empty()
    for i, audio_file in enumerate(audio_files):
        try:
            progress = ((i + 1) / len(audio_files)) * 100
            print(f"\rFusing audio: {progress:.1f}% ({i + 1}/{len(audio_files)})", end="", flush=True)
            audio = AudioSegment.from_mp3(audio_file)
            combined += audio
        except Exception as e:
            print(f"\nError loading {audio_file}: {e}")
            continue
            
    print(f"\rFusing audio: 100.0% ({len(audio_files)}/{len(audio_files)}) - Complete!")
    
    try:
        combined.export(output_path, format="mp3")
        print(f"Combined audiobook saved: {output_path}")
        return True
    except Exception as e:
        print(f"Error exporting combined audio: {e}")
        return False

def generate_tts_from_text(story_text, title, story_dir):
    """Generate TTS for existing story text with voice tag support"""
    print("\n=== GENERATING TTS ===")
    safe_title = sanitize_title(title)
    tts_dir = story_dir / f"{safe_title}_tts_segments"
    tts_dir.mkdir(parents=True, exist_ok=True)
    
    has_voice_tags = bool(VOICE_PATTERN.search(story_text))
    if has_voice_tags:
        print("Found voice tags in story, processing with multiple voices...")
        audio_files = generate_tts_with_voice_tags(story_text, tts_dir)
    else:
        print("No voice tags found, processing with default voice (af_heart)...")
        audio_files = generate_tts_without_voice_tags(story_text, tts_dir)
        
    if audio_files:
        combined_path = story_dir / f"{safe_title}_audiobook.mp3"
        success = combine_audio_files(audio_files, str(combined_path))
        
        if success:
            # Aggressively clean up the temporary segments directory
            try:
                shutil.rmtree(tts_dir)
                print(f"Cleaned up temporary segments directory: {tts_dir}")
            except Exception as e:
                print(f"Error cleaning up segments directory: {e}")
                
    return audio_files

def generate_tts_with_voice_tags(story_text, tts_dir):
    """Generate TTS for text that has voice tags - supports multiple voices"""
    segments = parse_tts_text(story_text)
    if not segments:
        print("No TTS segments found!")
        return []
    print(f"Generating TTS for {len(segments)} voice segments...")
    audio_files = []
    for i, segment in enumerate(segments):
        if not segment['text'].strip():
            continue
        voice = segment['voice']
        if voice not in VALID_VOICES:
            print(f"\nWarning: Invalid voice '{voice}', using af_heart instead")
            voice = "af_heart"
        try:
            progress_percent = ((i + 1) / len(segments)) * 100
            print(f"\rTTS Generation: {progress_percent:.1f}% ({i + 1}/{len(segments)}) [{voice}]", end="", flush=True)
            sentences = re.split(r'[.!?]+', segment['text'])
            sentences = [s.strip() for s in sentences if s.strip()]
            for j, sentence in enumerate(sentences):
                if not sentence.strip():
                    continue
                try:
                    with tts_client.audio.speech.with_streaming_response.create(
                        model="kokoro",
                        voice=voice,
                        input=sentence
                    ) as response:
                        audio_file = tts_dir / f"segment_{i:03d}_{j:02d}_{voice}.mp3"
                        response.stream_to_file(str(audio_file))
                        audio_files.append(str(audio_file))
                except Exception as e:
                    print(f"\nError generating TTS for segment {i} sentence {j}: {e}")
        except Exception as e:
            print(f"\nError generating TTS for segment {i}: {e}")
    print(f"\rTTS Generation: 100.0% ({len(segments)}/{len(segments)}) - Complete!")
    return audio_files

def generate_tts_without_voice_tags(story_text, tts_dir):
    """Generate TTS for text without voice tags (paragraph by paragraph with default voice)"""
    paragraphs = split_into_paragraphs(story_text)
    if not paragraphs:
        print("No text content found!")
        return []
    print(f"Generating TTS for {len(paragraphs)} paragraphs with af_heart...")
    audio_files = []
    for i, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            continue
        try:
            progress_percent = ((i + 1) / len(paragraphs)) * 100
            print(f"\rTTS Generation: {progress_percent:.1f}% ({i + 1}/{len(paragraphs)})", end="", flush=True)
            sentences = re.split(r'[.!?]+', paragraph)
            sentences = [s.strip() for s in sentences if s.strip()]
            for j, sentence in enumerate(sentences):
                if not sentence.strip():
                    continue
                try:
                    with tts_client.audio.speech.with_streaming_response.create(
                        model="kokoro",
                        voice="af_heart",
                        input=sentence
                    ) as response:
                        audio_file = tts_dir / f"paragraph_{i:04d}_sentence_{j:02d}_af_heart.mp3"
                        response.stream_to_file(str(audio_file))
                        audio_files.append(str(audio_file))
                except Exception as e:
                    print(f"\nError generating TTS for paragraph {i} sentence {j}: {e}")
        except Exception as e:
            print(f"\nError generating TTS for paragraph {i}: {e}")
    if paragraphs:
        print(f"\rTTS Generation: 100.0% ({len(paragraphs)}/{len(paragraphs)}) - Complete!")
    return audio_files

def generate_tts_for_existing_file():
    """Generate TTS for an existing story file - automatically uses _tts.txt if available"""
    print("=== TTS GENERATOR FOR EXISTING FILES ===")
    story_files = []
    # Main directory subfolders
    for story_dir in Path(OUTPUT_DIR).iterdir():
        if story_dir.is_dir():
            story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    # Series directories
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            for story_dir in series_dir.iterdir():
                if story_dir.is_dir():
                    story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    
    if not story_files:
        print("No story files found!")
        return
    
    print("Available story files:")
    for i, file in enumerate(story_files):
        try:
            rel_path = file.relative_to(Path(OUTPUT_DIR).parent)
        except:
            rel_path = file
        print(f"{i+1}. {rel_path}")
    
    try:
        choice = int(input("Select file number: ")) - 1
        selected_file = story_files[choice]
        
        # Check for _tts.txt version in the same directory
        tts_path = selected_file.parent / f"{selected_file.stem}_tts.txt"
        if tts_path.exists():
            with open(tts_path, 'r') as f:
                story_content = f.read()
            print("Using tagged version for TTS.")
        else:
            with open(selected_file, 'r') as f:
                story_content = f.read()
            print("No tagged version found, using clean version.")
        
        title = selected_file.stem
        print(f"Generating TTS for: {title}")
        generate_tts_from_text(story_content, title, selected_file.parent)
        print(f"TTS generation complete for {title}")
    except (ValueError, IndexError):
        print("Invalid selection")
    except Exception as e:
        print(f"Error: {e}")

def clean_story_file():
    """Remove voice tags from an existing story file"""
    print("=== CLEAN STORY FILE ===")
    story_files = []
    # Main directory subfolders
    for story_dir in Path(OUTPUT_DIR).iterdir():
        if story_dir.is_dir():
            story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_cleaned.txt")])
    # Series directories
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            for story_dir in series_dir.iterdir():
                if story_dir.is_dir():
                    story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_cleaned.txt")])
    
    if not story_files:
        print("No story files found!")
        return
    
    print("Available story files:")
    for i, file in enumerate(story_files):
        try:
            rel_path = file.relative_to(Path(OUTPUT_DIR).parent)
        except:
            rel_path = file
        print(f"{i+1}. {rel_path}")
    
    try:
        choice = int(input("Select file number to clean: ")) - 1
        selected_file = story_files[choice]
        with open(selected_file, 'r') as f:
            story_content = f.read()
        clean_content = remove_voice_tags(story_content)
        clean_filename = f"{selected_file.stem}_cleaned{selected_file.suffix}"
        clean_filepath = selected_file.parent / clean_filename
        with open(clean_filepath, 'w') as f:
            f.write(clean_content)
        print(f"Cleaned story saved: {clean_filepath}")
    except (ValueError, IndexError):
        print("Invalid selection")
    except Exception as e:
        print(f"Error: {e}")

def create_worldbook_interactive():
    """Interactive worldbook creation"""
    print("=== CREATE NEW WORLDBOOK ===")
    name = input("Worldbook name (will be filename): ").strip()
    if not name:
        print("Name required!")
        return
    print("Enter worldbook content (multiple lines, empty line to finish):")
    lines = []
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    content = '\n'.join(lines)
    worldbook_path = Path(WORLDBOOK_DIR) / f"{name}.txt"
    with open(worldbook_path, 'w') as f:
        f.write(content)
    print(f"Worldbook created: {worldbook_path}")

def run_debug_mode():
    """Run debug mode with test story - no AI generation needed"""
    print("=== DEBUG MODE - Using Test Story ===")
    story = TEST_STORY
    title = "Debug Test Story"
    print(f"\nTest story preview:\n{story[:200]}...\n")
    
    filepath, story_dir = save_story(story, title)
    tts_filepath = save_tts_story(story, title, story_dir)
    
    voices_used = extract_voices_used(story)
    save_metadata(title, "standalone", None, None, [], story_dir, voices_used)
    print(f"\nVoices used in test story: {voices_used}")
    
    want_tts = get_tts_preference()
    if want_tts:
        generate_tts_from_text(story, title, story_dir)
        
    print(f"\n🎉 Debug process completed!")
    print(f"📖 Main story (no tags): {filepath}")
    print(f"📄 TTS story (with tags): {tts_filepath}")

def main():
    parser = argparse.ArgumentParser(description='Enhanced Story Generator with Series & Worldbooks')
    parser.add_argument('--tts-only', action='store_true', help='Generate TTS for existing story file')
    parser.add_argument('--create-worldbook', action='store_true', help='Create a new worldbook')
    parser.add_argument('--show-help', action='store_true', help='Show help information')
    parser.add_argument('--clean-story', action='store_true', help='Remove voice tags from existing story file')
    parser.add_argument('--debug', action='store_true', help='Use test story for debugging (no AI generation)')
    args = parser.parse_args()

    if args.show_help:
        show_help()
        return

    ensure_directories()

    if args.debug:
        run_debug_mode()
        return

    if args.create_worldbook:
        create_worldbook_interactive()
        return

    if args.tts_only:
        generate_tts_for_existing_file()
        return

    if args.clean_story:
        clean_story_file()
        return

    try:
        # Get user input
        topic, genre = get_user_input()
        story_type = get_story_type()
        
        # Series Management Logic
        reference_story = None
        series_name = None
        
        if story_type in ["sequel", "prequel"]:
            reference_story = select_reference_story()
            if reference_story:
                # Determine series from reference path
                try:
                    rel_path = reference_story.relative_to(Path(SERIES_DIR))
                    # Path: SERIES_DIR/SeriesName/StoryName/StoryName.txt
                    series_name = rel_path.parts[0]
                except ValueError:
                    # It's in OUTPUT_DIR, meaning it was standalone.
                    print("Reference story is standalone. Creating a new series for this continuation.")
                    series_name = input("Enter series name: ").strip() or "NewSeries"
            else:
                print("No reference selected. Proceeding as standalone.")
                story_type = "standalone"
        
        if story_type == "standalone":
            choice = input("Is this part of a series? (y/n, default=n): ").strip().lower()
            if choice == "y":
                print("Available series:")
                series_dirs = [d.name for d in Path(SERIES_DIR).iterdir() if d.is_dir()]
                for i, s in enumerate(series_dirs, 1):
                    print(f"{i}. {s}")
                print(f"{len(series_dirs)+1}. Create new series")
                s_choice = input("Select series: ").strip()
                if s_choice.isdigit() and 1 <= int(s_choice) <= len(series_dirs):
                    series_name = series_dirs[int(s_choice)-1]
                else:
                    series_name = input("Enter new series name: ").strip()
            else:
                series_name = None

        # Load story context (prefers _tts.txt version for voice consistency)
        story_context = load_story_context(reference_story)
        
        # Select worldbook context
        worldbook_path = select_worldbook()
        worldbook_context = load_worldbook_context(worldbook_path)
        
        # Get features and length
        features = get_required_features()
        length_instruction = get_story_length()
        
        # Ask about TTS preference upfront
        want_tts = get_tts_preference()
        
        # Build voice instruction
        voice_instruction = build_voice_instruction()
        
        # Generate outline (passes worldbook_context and voice_instruction)
        outline = generate_outline(
            topic, genre, features,
            worldbook_context, story_context,
            length_instruction, story_type, voice_instruction
        )
        
        # Extract chapter count
        total_chapters = extract_chapter_count(outline)
        
        # Write story (passes worldbook_context and voice_instruction)
        story = write_story(outline, total_chapters, worldbook_context, story_context, voice_instruction)
        
        # Extract title
        title = extract_title(outline)
        
        # Save clean version (main .txt + .pdf) in dedicated folder
        filepath, story_dir = save_story(story, title, series_name)
        
        # Save tagged version for TTS
        tts_filepath = save_tts_story(story, title, story_dir)
        
        # Extract voices used and save metadata
        voices_used = extract_voices_used(story)
        save_metadata(title, story_type, reference_story, worldbook_path, features, story_dir, voices_used)
        
        # Generate TTS if requested (uses tagged version)
        if want_tts:
            generate_tts_from_text(story, title, story_dir)
            
        print(f"\n🎉 Process completed successfully!")
        print(f"📖 Main story (no tags): {filepath}")
        print(f"📄 TTS story (with tags): {tts_filepath}")
        print(f"🎙️ Voices used: {voices_used}")
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    main()