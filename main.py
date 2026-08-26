import argparse
import re
import openai
import os
from pathlib import Path
import time
import requests
import sys

# Configuration
STORY_MODEL = "Aion-3.0"
TITLE_MODEL = "Venice-Uncensored"
BASE_URL = "http://10.36.72.1:3000/v1"
TTS_URL = "http://10.36.72.1:8880/v1"
BASE_PROMPT_PATH = "/mnt/devdrive/Files/raph/dreams/books/prompts/base.txt"
OUTPUT_DIR = "/mnt/devdrive/Files/raph/dreams/books"
VOICE_PATTERN = re.compile(r"<(am|af|bm|bf|ef|em|ff|hf|hm|if|im|jf|jm|pf|pm|zf|zm)_[^>]+>([^<]*)</\1_[^>]+>")
QUOTE_PATTERN = re.compile(r'(["“”])([^"“”]*?)\1')  # Matches quoted text

# Initialize OpenAI clients
llm_client = openai.OpenAI(base_url=BASE_URL, api_key="dummy-key")
tts_client = openai.OpenAI(base_url=TTS_URL, api_key="not-needed")

def read_base_prompt():
    """Read the base prompt from file"""
    try:
        with open(BASE_PROMPT_PATH, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

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

def get_user_input():
    """Get story topic and length from user"""
    print("=== STORY GENERATOR ===")
    
    # Get story topic
    topic = input("What should the story be about? (leave blank for AI to decide): ").strip()
    if not topic:
        topic = "a compelling story of your choice"
    
    # Get story length
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
    
    return topic, length_instruction

def generate_outline(topic, length_instruction):
    """Generate story outline with streaming"""
    base_prompt = read_base_prompt()
    prompt = f"{base_prompt}\n\nGenerate a detailed story outline about {topic}. {length_instruction}. List each chapter with a brief description."
    
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
            print(f"⚠️  WARNING: Chapter {chapter_num} still missing voice tags!")
    
    return "\n\n".join(story_parts)

def extract_title(story):
    """Extract or generate title from story"""
    print("=== PHASE 3: GENERATING TITLE ===")
    
    # Try to extract from story first
    lines = story.split('\n')
    for line in lines[:10]:
        if line.strip() and not line.startswith('#') and len(line.strip()) > 10:
            extracted_title = line.replace('*', '').replace('"', '').strip()[:50]
            if len(extracted_title) > 5:
                print(f"Using extracted title: {extracted_title}")
                return extracted_title
    
    # Generate title if extraction fails
    try:
        prompt = f"Based on this story, create ONE compelling title (max 50 characters):\n\n{story[:1000]}"
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
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1F.]', '_', title)[:100]
    # Remove trailing underscores
    safe_title = safe_title.rstrip('_')
    return safe_title or "Untitled-Story"

def save_story(story, title):
    """Save story to file"""
    # Sanitize title for filename
    safe_title = sanitize_title(title)
    filename = f"{safe_title}.txt"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    # Ensure directory exists
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    with open(filepath, 'w') as f:
        f.write(story)
    
    print(f"Story saved: {filepath}")
    return filepath

def remove_voice_tags(text):
    """Remove voice tags from text"""
    # Remove all voice tags like <af_heart>...</af_heart>
    clean_text = VOICE_PATTERN.sub(r'\2', text)
    return clean_text

def save_clean_story(story, title):
    """Save a clean version of the story without voice tags"""
    safe_title = sanitize_title(title)
    clean_story = remove_voice_tags(story)
    
    # Create tts_text subdirectory
    tts_text_dir = os.path.join(OUTPUT_DIR, "tts_text")
    Path(tts_text_dir).mkdir(parents=True, exist_ok=True)
    
    # Save clean version
    clean_filename = f"{safe_title}_clean.txt"
    clean_filepath = os.path.join(tts_text_dir, clean_filename)
    
    with open(clean_filepath, 'w') as f:
        f.write(clean_story)
    
    print(f"Clean story saved: {clean_filepath}")
    return clean_filepath

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

def generate_tts_from_text(story_text, title):
    """Generate TTS for existing story text with paragraph-level processing"""
    print("\n=== GENERATING TTS FROM EXISTING TEXT ===")
    
    # First check if story has voice tags
    has_voice_tags = bool(VOICE_PATTERN.search(story_text))
    
    # Create output directory
    safe_title = sanitize_title(title)
    tts_dir = os.path.join(OUTPUT_DIR, f"{safe_title}_tts")
    Path(tts_dir).mkdir(parents=True, exist_ok=True)
    
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
                        audio_file = os.path.join(tts_dir, f"segment_{i:03d}_{j:02d}_{segment['voice']}.mp3")
                        response.stream_to_file(audio_file)
                        audio_files.append(audio_file)
                        
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
                        audio_file = os.path.join(tts_dir, f"paragraph_{i:04d}_sentence_{j:02d}_af_heart.mp3")
                        response.stream_to_file(audio_file)
                        audio_files.append(audio_file)
                        
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
    
    # List available story files
    story_files = list(Path(OUTPUT_DIR).glob("*.txt"))
    if not story_files:
        print("No story files found!")
        return
    
    print("Available story files:")
    for i, file in enumerate(story_files):
        print(f"{i+1}. {file.name}")
    
    try:
        choice = int(input("Select file number: ")) - 1
        selected_file = story_files[choice]
        
        # Read the story content
        with open(selected_file, 'r') as f:
            story_content = f.read()
        
        # Use filename as title (without extension)
        title = selected_file.stem
        print(f"Generating TTS for: {title}")
        
        # Generate TTS
        audio_files = generate_tts_from_text(story_content, title)
        print(f"TTS generation complete for {title}")
        
    except (ValueError, IndexError):
        print("Invalid selection")
    except Exception as e:
        print(f"Error: {e}")

def main():
    parser = argparse.ArgumentParser(description='Story Generator with TTS')
    parser.add_argument('--tts-only', action='store_true', help='Generate TTS for existing story file')
    args = parser.parse_args()
    
    if args.tts_only:
        generate_tts_for_existing_file()
        return
    
    try:
        # Get user input
        topic, length_instruction = get_user_input()
        
        # Generate outline
        outline = generate_outline(topic, length_instruction)
        
        # Extract chapter count
        total_chapters = extract_chapter_count(outline)
        
        # Write story
        story = write_story(outline, total_chapters)
        
        # Extract title and save
        title = extract_title(story)
        filepath = save_story(story, title)
        
        # Save clean version for TTS
        clean_filepath = save_clean_story(story, title)
        
        # Offer to generate TTS
        if input("\nGenerate TTS now? (y/n): ").lower().startswith('y'):
            generate_tts_from_text(story, title)
        
        print(f"\n🎉 Process completed successfully!")
        print(f"📖 Story: {filepath}")
        print(f"📄 Clean text: {clean_filepath}")
        
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    main()
