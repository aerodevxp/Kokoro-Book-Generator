import argparse
import re
import openai
import os
from pathlib import Path
import time
import requests
import sys
import json
from dotenv import load_dotenv

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

# Precompiled regex patterns
VOICE_PATTERN = re.compile(r"<(am|af|bm|bf|ef|em|ff|hf|hm|if|im|jf|jm|pf|pm|zf|zm)*[^>]+>([^<]*)</\1*[^>]+>")
QUOTE_PATTERN = re.compile(r'(["“”])([^"“”]*)\1')  # Matches quoted text

# Initialize OpenAI clients
llm_client = openai.OpenAI(base_url=BASE_URL, api_key=os.getenv("LLM_API_KEY", "dummy-key"))
tts_client = openai.OpenAI(base_url=TTS_URL, api_key=os.getenv("TTS_API_KEY", "not-needed"))

def show_help():
    """Display help information"""
    help_text = """
=== STORY GENERATOR HELP ===

This script generates stories with the following features:
- Customizable story parameters (topic, genre, length, features)
- Series management (sequels/prequels)
- Worldbook integration (contextual world information)
- Automatic TTS generation
- Metadata tracking

Workflow:
1. Topic & Genre: What the story is about
2. Story Type: Standalone, sequel, or prequel
3. References: Select existing books/worldbooks for context
4. Features: Required story elements
5. Length: Chapter count
6. Generation: Outline → Story → Title
7. Output: Save files, generate TTS

File Structure:
/books/
  ├── story.txt              # Generated stories (NO voice tags - main file)
  ├── unclean_texts/         # Directory for unclean versions (with voice tags)
  │   └── story_unclean.txt  # Version WITH voice tags (for TTS)
  ├── worldbooks/            # World context files (.txt)
  ├── series/                # Organized story series
  │   └── SERIES_NAME/
  │       ├── book1.txt
  │       └── unclean_texts/
  │           └── book1_unclean.txt
  └── features.txt           # Available story features

Commands:
  --show-help         Show this help
  --tts-only          Generate TTS for existing story file
  --create-worldbook  Create new worldbook interactively
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

                    # Update spinner every 15 tokens
                    if token_count % 15 == 0:
                        show_spinner.counter += 1
                        sys.stdout.write(f"\r{show_spinner(label)} ({token_count} tokens)")
                        sys.stdout.flush()

        print(f"\r{label}... Done! ({token_count} tokens)")
        return full_content
    except Exception as e:
        print(f"\nError during streaming: {e}")
        raise

def load_features():
    """Load available features from file"""
    features = []
    try:
        with open(FEATURES_FILE, 'r') as f:
            features = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Features file not found: {FEATURES_FILE}")
        # Create default features file
        default_features = [
            "Magic System",
            "Political Intrigue",
            "Romantic Subplot",
            "Mystery Element",
            "Action Sequences",
            "Character Development",
            "Philosophical Themes",
            "Supernatural Elements",
            "Technology Integration",
            "Survival Elements"
        ]
        with open(FEATURES_FILE, 'w') as f:
            f.write('\n'.join(default_features))
        features = default_features
        print(f"Created default features file: {FEATURES_FILE}")
    
    return features

def get_user_input():
    """Get story topic and genre from user"""
    print("=== STORY GENERATOR ===")

    # Get story topic
    topic = input("What should the story be about? (leave blank for AI to decide): ").strip()
    if not topic:
        topic = "a compelling story of your choice"

    # Get genre as free text
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
    # Collect all story files
    story_files = []
    # Check main directory
    story_files.extend(list(Path(OUTPUT_DIR).glob("*.txt")))
    # Check series directories
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            story_files.extend(list(series_dir.glob("*.txt")))
    
    # Filter out metadata and files in unclean_texts directories
    filtered_files = []
    for f in story_files:
        # Skip metadata files
        if f.name.endswith("_metadata.json"):
            continue
        # Skip files in unclean_texts directories
        if "unclean_texts" in str(f):
            continue
        filtered_files.append(f)
    story_files = filtered_files
    
    if not story_files:
        print("No existing stories found.")
        return None
    
    print("\nAvailable stories:")
    for i, story in enumerate(story_files, 1):
        rel_path = story.relative_to(Path(OUTPUT_DIR).parent)
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
    """Load story content for context injection"""
    if not story_path:
        return ""
    
    try:
        with open(story_path, 'r') as f:
            content = f.read()[:1500]  # First 1500 chars for context
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
            content = f.read()[:1000]  # First 1000 chars for context
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
        "4": "Decide the optimal chapter count yourself"
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

def generate_outline(topic, genre, features, worldbook_context, story_context, length_instruction, story_type):
    """Generate story outline with streaming"""
    base_prompt = read_base_prompt()
    
    # Build features instruction
    features_instruction = ""
    if features:
        features_list = ", ".join(features)
        features_instruction = f"The story MUST include these elements: {features_list}. "
    
    # Build story type instruction
    type_instruction = ""
    if story_type == "sequel":
        type_instruction = "This is a SEQUEL - continue the story logically from previous events while introducing new conflicts."
    elif story_type == "prequel":
        type_instruction = "This is a PREQUEL - explore events leading up to referenced story with established characters/settings."
    
    prompt = f"""{base_prompt}

{worldbook_context}{story_context}Generate a detailed story outline.
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
    # Look for chapter headings
    chapter_matches = re.findall(r'(?:Chapter|chapter)\s+(\d+)', outline, re.IGNORECASE)
    if chapter_matches:
        chapter_numbers = [int(x) for x in chapter_matches]
        actual_chapters = max(chapter_numbers)
        print(f"Detected {actual_chapters} chapters in outline")
        return actual_chapters

    # Fallback if no chapters detected
    print("No chapters detected, defaulting to 10")
    return 10

def add_missing_voice_tags(text):
    """Add voice tags to quoted dialogue that's missing them"""
    print("Post-processing: Adding missing voice tags...")

    # Find quoted text that isn't already in voice tags
    def replace_quotes(match):
        quote_content = match.group(2).strip()
        if quote_content:
            # Wrap in default voice tag
            return f'<af_heart>{quote_content}</af_heart>'
        return match.group(0)

    # Replace unmatched quotes with voice tags
    processed_text = QUOTE_PATTERN.sub(replace_quotes, text)

    # Count added tags
    original_quotes = len(QUOTE_PATTERN.findall(text))
    voice_tagged = len(VOICE_PATTERN.findall(processed_text))

    if voice_tagged > 0:
        print(f"Added voice tags to {voice_tagged} dialogue segments")

    return processed_text

def write_story(outline, total_chapters):
    """Write the full story chapter by chapter with enforced voice tags"""
    print(f"\n=== PHASE 2: WRITING STORY ({total_chapters} CHAPTERS) ===")
    base_prompt = read_base_prompt()
    story_parts = []

    for chapter_num in range(1, total_chapters + 1):
        if chapter_num == 1:
            prompt = f"{base_prompt}\n\nBased on this outline:\n{outline}\n\nWrite Chapter {chapter_num} in detail. CRITICALLY IMPORTANT: Include dialogue with voice tags like <af_nicole>dialogue</af_nicole> OR <am_david>dialogue</am_david> for ALL character dialogue. EVERY piece of dialogue MUST have voice tags. Use different voices for different characters consistently."
        else:
            prev_content = ' '.join(story_parts[-1:])  # Just previous chapter
            prompt = f"{base_prompt}\n\nContinue the story from:\n{prev_content}\n\nWrite Chapter {chapter_num} in detail. CRITICALLY IMPORTANT: Include dialogue with voice tags like <af_nicole>dialogue</af_nicole> OR <am_david>dialogue</am_david> for ALL character dialogue. EVERY piece of dialogue MUST have voice tags. Use different voices for different characters consistently."

        # Always end chapter with [END]
        prompt += " End this chapter with [END]"

        response = llm_client.chat.completions.create(
            model=STORY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.8,
            stream=True
        )

        chapter = stream_with_spinner(response, f"Writing chapter {chapter_num}/{total_chapters}")

        # Post-process to add missing voice tags
        chapter_with_tags = add_missing_voice_tags(chapter)
        story_parts.append(chapter_with_tags)
        print(f"Chapter {chapter_num}: Completed\n")

        # Debug: Check if chapter has voice tags
        if not VOICE_PATTERN.search(chapter_with_tags):
            print(f"⚠  WARNING: Chapter {chapter_num} still missing voice tags!")

    return "\n\n".join(story_parts)

def extract_title(storyOutline):
    """Extract or generate title from story"""
    print("=== PHASE 3: GENERATING TITLE ===")

    # Generate title if extraction fails
    try:
        prompt = f"Based on the following story outline, create ONE compelling title:\n\n{storyOutline}"
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
    """Sanitize title for filename (remove periods and other problematic chars)"""
    # Remove periods and other problematic characters
    safe_title = re.sub(r'[<>:"/|?\*\x00-\x1F.]', '_', title)[:100]
    # Remove trailing underscores
    safe_title = safe_title.rstrip('_')
    return safe_title or "Untitled-Story"

def save_metadata(title, story_type, reference_story, worldbook_used, features_used, output_dir):
    """Save metadata for the story"""
    metadata = {
        "title": title,
        "story_type": story_type,
        "reference_story": str(reference_story) if reference_story else None,
        "worldbook": str(worldbook_used) if worldbook_used else None,
        "features": features_used,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(output_dir)
    }
    
    metadata_file = output_dir / f"{sanitize_title(title)}_metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"Metadata saved: {metadata_file}")

def save_story(story, title, series_name=None):
    """Save story WITHOUT voice tags (main story file)"""
    # Sanitize title for filename
    safe_title = sanitize_title(title)
    
    # Determine save location
    if series_name:
        save_dir = Path(SERIES_DIR) / series_name
    else:
        save_dir = Path(OUTPUT_DIR)
    
    save_dir.mkdir(parents=True, exist_ok=True)
    
    # Save clean version as main story file
    clean_story = remove_voice_tags(story)
    filepath = save_dir / f"{safe_title}.txt"
    
    with open(filepath, 'w') as f:
        f.write(clean_story)

    print(f"Main story (no voice tags) saved: {filepath}")
    return filepath, save_dir

def remove_voice_tags(text):
    """Remove voice tags from text"""
    # Remove all voice tags like <af_heart>...</af_heart>
    clean_text = VOICE_PATTERN.sub(r'\2', text)
    return clean_text

def save_unclean_story(story, title, series_save_dir):
    """Save the unclean version WITH voice tags (for TTS) in unclean_texts subdirectory"""
    safe_title = sanitize_title(title)
    
    # Create unclean_texts subdirectory
    unclean_dir = series_save_dir / "unclean_texts"
    unclean_dir.mkdir(parents=True, exist_ok=True)
    
    # Save unclean version with tags
    unclean_filename = f"{safe_title}_unclean.txt"
    unclean_filepath = unclean_dir / unclean_filename

    with open(unclean_filepath, 'w') as f:
        f.write(story)

    print(f"Unclean story (with voice tags) saved: {unclean_filepath}")
    return unclean_filepath

def split_into_paragraphs(text):
    """Split text into paragraphs for better TTS granularity"""
    # Split by double newlines (paragraph breaks)
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    return paragraphs

def parse_tts_text(text):
    """Parse text and extract voice segments"""
    segments = []
    last_end = 0

    matches = list(VOICE_PATTERN.finditer(text))

    for match in matches:
        # Add default voice segment before this match
        if match.start() > last_end:
            default_text = text[last_end:match.start()].strip()
            if default_text:
                segments.append({
                    'voice': 'af_heart',
                    'text': default_text
                })

        # Add matched voice segment
        voice_tag = match.group(0)
        voice = voice_tag[1:voice_tag.find('>')]
        content = match.group(2)
        segments.append({
            'voice': voice,
            'text': content.strip()
        })
        last_end = match.end()

    # Add remaining text with default voice
    if last_end < len(text):
        remaining_text = text[last_end:].strip()
        if remaining_text:
            segments.append({
                'voice': 'af_heart',
                'text': remaining_text
            })

    return segments

def generate_tts_from_text(story_text, title, series_save_dir):
    """Generate TTS for existing story text with paragraph-level processing"""
    print("\n=== GENERATING TTS FROM EXISTING TEXT ===")

    # First check if story has voice tags
    has_voice_tags = bool(VOICE_PATTERN.search(story_text))

    # Create output directory
    safe_title = sanitize_title(title)
    tts_dir = series_save_dir / f"{safe_title}_tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    if has_voice_tags:
        # Process with voice tags
        print("Found voice tags in story, processing segments...")
        return generate_tts_with_voice_tags(story_text, tts_dir)
    else:
        # Process without voice tags (paragraph by paragraph)
        print("No voice tags found, processing paragraph by paragraph...")
        return generate_tts_without_voice_tags(story_text, tts_dir)

def generate_tts_with_voice_tags(story_text, tts_dir):
    """Generate TTS for text that has voice tags"""
    # Parse text into voice segments
    segments = parse_tts_text(story_text)

    if not segments:
        print("No TTS segments found!")
        return []

    print(f"Generating TTS for {len(segments)} voice segments...")
    audio_files = []

    # Process each voice segment
    for i, segment in enumerate(segments):
        if not segment['text'].strip():
            continue

        try:
            # Show progress
            progress_percent = ((i + 1) / len(segments)) * 100
            print(f"\rTTS Generation: {progress_percent:.1f}% ({i + 1}/{len(segments)})", end="", flush=True)

            # Split long segments into sentences for better progress
            sentences = re.split(r'[.!?]+', segment['text'])
            sentences = [s.strip() for s in sentences if s.strip()]

            # Generate TTS for each sentence
            for j, sentence in enumerate(sentences):
                if not sentence.strip():
                    continue

                try:
                    with tts_client.audio.speech.with_streaming_response.create(
                        model="kokoro",
                        voice=segment['voice'],
                        input=sentence
                    ) as response:
                        audio_file = tts_dir / f"segment_{i:03d}_{j:02d}_{segment['voice']}.mp3"
                        response.stream_to_file(str(audio_file))
                        audio_files.append(str(audio_file))

                except Exception as e:
                    print(f"\nError generating TTS for segment {i} sentence {j}: {e}")

        except Exception as e:
            print(f"\nError generating TTS for segment {i}: {e}")

    print(f"\rTTS Generation: 100.0% ({len(segments)}/{len(segments)}) - Complete!")
    print(f"\nTTS generation complete. {len(audio_files)} files saved to: {tts_dir}")
    return audio_files

def generate_tts_without_voice_tags(story_text, tts_dir):
    """Generate TTS for text without voice tags (paragraph by paragraph)"""
    # Split into paragraphs for granular processing
    paragraphs = split_into_paragraphs(story_text)

    if not paragraphs:
        print("No text content found!")
        return []

    print(f"Generating TTS for {len(paragraphs)} paragraphs...")
    audio_files = []

    # Process each paragraph with proper progress tracking
    for i, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            continue

        try:
            # Calculate progress percentage
            progress_percent = ((i + 1) / len(paragraphs)) * 100
            print(f"\rTTS Generation: {progress_percent:.1f}% ({i + 1}/{len(paragraphs)})", end="", flush=True)

            # Split paragraph into sentences for better progress granularity
            sentences = re.split(r'[.!?]+', paragraph)
            sentences = [s.strip() for s in sentences if s.strip()]

            # Generate audio for each sentence in paragraph
            for j, sentence in enumerate(sentences):
                if not sentence.strip():
                    continue

                try:
                    # Generate TTS for this sentence with default voice
                    with tts_client.audio.speech.with_streaming_response.create(
                        model="kokoro",
                        voice="af_heart",  # Default voice
                        input=sentence
                    ) as response:
                        audio_file = tts_dir / f"paragraph_{i:04d}_sentence_{j:02d}_af_heart.mp3"
                        response.stream_to_file(str(audio_file))
                        audio_files.append(str(audio_file))

                except Exception as e:
                    print(f"\nError generating TTS for paragraph {i} sentence {j}: {e}")

        except Exception as e:
            print(f"\nError generating TTS for paragraph {i}: {e}")

    # Final progress update
    if paragraphs:
        print(f"\rTTS Generation: 100.0% ({len(paragraphs)}/{len(paragraphs)}) - Complete!")

    print(f"\nTTS generation complete. {len(audio_files)} files saved to: {tts_dir}")
    return audio_files

def generate_tts_for_existing_file():
    """Generate TTS for an existing story file"""
    print("=== TTS GENERATOR FOR EXISTING FILES ===")

    # List available story files (looking for unclean versions with voice tags)
    unclean_files = []
    # Main directory
    main_unclean_dir = Path(OUTPUT_DIR) / "unclean_texts"
    if main_unclean_dir.exists():
        unclean_files.extend(list(main_unclean_dir.glob("*_unclean.txt")))
    # Series directories
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            series_unclean_dir = series_dir / "unclean_texts"
            if series_unclean_dir.exists():
                unclean_files.extend(list(series_unclean_dir.glob("*_unclean.txt")))
    
    if not unclean_files:
        print("No unclean story files found!")
        return

    print("Available unclean story files (with voice tags):")
    for i, file in enumerate(unclean_files):
        rel_path = file.relative_to(Path(OUTPUT_DIR).parent)
        print(f"{i+1}. {rel_path}")

    try:
        choice = int(input("Select file number: ")) - 1
        selected_file = unclean_files[choice]

        # Read the story content (WITH voice tags for proper TTS)
        with open(selected_file, 'r') as f:
            story_content = f.read()

        # Use filename as title (without _unclean.txt extension)
        title = selected_file.stem.replace('_unclean', '')
        print(f"Generating TTS for: {title}")

        # Generate TTS
        audio_files = generate_tts_from_text(story_content, title, selected_file.parent.parent)
        print(f"TTS generation complete for {title}")

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

def main():
    parser = argparse.ArgumentParser(description='Enhanced Story Generator with Series & Worldbooks')
    parser.add_argument('--tts-only', action='store_true', help='Generate TTS for existing story file')
    parser.add_argument('--create-worldbook', action='store_true', help='Create a new worldbook')
    parser.add_argument('--show-help', action='store_true', help='Show help information')
    args = parser.parse_args()

    # Handle help flag
    if args.show_help:
        show_help()
        return

    # Ensure directories exist
    ensure_directories()

    if args.create_worldbook:
        create_worldbook_interactive()
        return
        
    if args.tts_only:
        generate_tts_for_existing_file()
        return

    try:
        # Get user input
        topic, genre = get_user_input()
        story_type = get_story_type()
        
        # Get reference story if needed
        reference_story = None
        if story_type in ["sequel", "prequel"]:
            reference_story = select_reference_story()
        
        # Load story context
        story_context = load_story_context(reference_story)
        
        # Select worldbook context
        worldbook_path = select_worldbook()
        worldbook_context = load_worldbook_context(worldbook_path)
        
        # Get features and length
        features = get_required_features()
        length_instruction = get_story_length()
        
        # Ask about TTS preference upfront
        want_tts = get_tts_preference()

        # Generate outline
        outline = generate_outline(
            topic, genre, features, 
            worldbook_context, story_context, 
            length_instruction, story_type
        )

        # Extract chapter count
        total_chapters = extract_chapter_count(outline)

        # Write story
        story = write_story(outline, total_chapters)

        # Extract title and save
        title = extract_title(outline)
        
        # For sequels/prequels, save in same series as reference
        series_name = None
        if story_type in ["sequel", "prequel"] and reference_story:
            ref_parent = reference_story.parent
            if ref_parent != Path(OUTPUT_DIR):
                series_name = ref_parent.name
        
        filepath, save_dir = save_story(story, title, series_name)
        
        # Save unclean version with voice tags for TTS in unclean_texts subdirectory
        unclean_filepath = save_unclean_story(story, title, save_dir)

        # Save metadata
        save_metadata(title, story_type, reference_story, worldbook_path, features, save_dir)

        # Generate TTS if requested - now uses the unclean version
        if want_tts:
            generate_tts_from_text(story, title, save_dir)  # Use story with tags for TTS

        print(f"\n🎉 Process completed successfully!")
        print(f"📖 Main story (no tags): {filepath}")
        print(f"📄 Unclean story (with tags): {unclean_filepath}")

    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    main()
