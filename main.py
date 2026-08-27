import streamlit as st
import openai
import os
from pathlib import Path
import time
import json
import re
import shutil
from spire.doc import *
from dotenv import load_dotenv
from pydub import AudioSegment

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

TEST_STORY = """The coffee shop was quiet that afternoon. Rain pattered against the windows, creating a cozy atmosphere inside.

<af_bella>Can you believe it's been three years?</af_bella> Bella said, stirring her latte.

<am_adam>Time flies, doesn't it?</am_adam> Raph replied, leaning back in his chair. <am_adam>Feels like yesterday we were all in college together.</am_adam>

<af_nova>I miss those days.</af_nova> Nova sighed, looking out the window. <af_nova>Everything was so much simpler.</af_nova>

Bella nodded. <af_bella>Simpler, maybe. But I wouldn't trade where we are now for anything.</af_bella>

Raph smiled. <am_adam>To the future, then.</am_adam>

<af_nova>To the future.</af_nova>"""

# Initialize OpenAI clients
@st.cache_resource
def get_clients():
    llm = openai.OpenAI(base_url=BASE_URL, api_key=os.getenv("LLM_API_KEY", "dummy-key"))
    tts = openai.OpenAI(base_url=TTS_URL, api_key=os.getenv("TTS_API_KEY", "not-needed"))
    return llm, tts

llm_client, tts_client = get_clients()

# --- Helper Functions ---

def read_base_prompt():
    try:
        with open(BASE_PROMPT_PATH, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def ensure_directories():
    dirs = [OUTPUT_DIR, WORLDBOOK_DIR, SERIES_DIR]
    for directory in dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)

def string_to_pdf(string, outputFullPath):
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

def build_voice_instruction():
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

def get_all_stories():
    story_files = []
    for story_dir in Path(OUTPUT_DIR).iterdir():
        if story_dir.is_dir():
            story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    for series_dir in Path(SERIES_DIR).iterdir():
        if series_dir.is_dir():
            for story_dir in series_dir.iterdir():
                if story_dir.is_dir():
                    story_files.extend([f for f in story_dir.glob("*.txt") if not f.name.endswith("_metadata.json") and not f.name.endswith("_tts.txt") and not f.name.endswith("_cleaned.txt")])
    return story_files

def load_story_context(story_path):
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
    return VOICE_PATTERN.sub(r'\2', text)

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

# --- Streamlit UI & Core Logic ---

def main():
    st.set_page_config(page_title="Story Generator", page_icon="📖", layout="wide")
    ensure_directories()

    st.title("📖 Story Generator")
    st.markdown("Generate stories with AI, complete with multi-voice TTS audiobook generation.")

    # Sidebar Navigation
    menu = ["Generate New Story", "Generate TTS for Existing", "Story Library", "Worldbook Manager", "Feature Manager", "Clean Existing Story"]
    choice = st.sidebar.selectbox("Menu", menu)

    if choice == "Generate New Story":
        generate_new_story_page()
    elif choice == "Generate TTS for Existing":
        generate_tts_existing_page()
    elif choice == "Story Library":
        story_library_page()
    elif choice == "Worldbook Manager":
        worldbook_manager_page()
    elif choice == "Feature Manager":
        feature_manager_page()
    elif choice == "Clean Existing Story":
        clean_existing_story_page()

def generate_new_story_page():
    st.header("Generate New Story")
    
    with st.form("story_config"):
        col1, col2 = st.columns(2)
        with col1:
            topic = st.text_input("Topic", placeholder="Leave blank for AI to decide")
            genre = st.text_input("Genre", placeholder="Leave blank for AI to decide")
            story_type = st.selectbox("Story Type", ["standalone", "sequel", "prequel"])
            
            reference_story = None
            series_name = None
            
            if story_type in ["sequel", "prequel"]:
                stories = get_all_stories()
                story_opts = ["None"] + [str(s.relative_to(Path(OUTPUT_DIR).parent)) for s in stories]
                ref_choice = st.selectbox("Reference Story", story_opts)
                if ref_choice != "None":
                    reference_story = next(s for s in stories if str(s.relative_to(Path(OUTPUT_DIR).parent)) == ref_choice)
                    try:
                        rel_path = reference_story.relative_to(Path(SERIES_DIR))
                        series_name = rel_path.parts[0]
                    except ValueError:
                        series_name = st.text_input("Reference is standalone. Enter new series name:", value="NewSeries")
            else:
                is_series = st.checkbox("Is this part of a series?")
                if is_series:
                    series_dirs = [d.name for d in Path(SERIES_DIR).iterdir() if d.is_dir()]
                    series_opts = ["Create New Series"] + series_dirs
                    s_choice = st.selectbox("Select Series", series_opts)
                    if s_choice == "Create New Series":
                        series_name = st.text_input("Enter new series name:")
                    else:
                        series_name = s_choice

        with col2:
            worldbooks = get_worldbooks()
            wb_opts = ["None"] + [wb.stem for wb in worldbooks]
            wb_choice = st.selectbox("Worldbook", wb_opts)
            worldbook_path = next((wb for wb in worldbooks if wb.stem == wb_choice), None) if wb_choice != "None" else None
            
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
            debug_mode = st.checkbox("Debug Mode (Use Test Story, skip AI)")
            
            submit_btn = st.form_submit_button("🚀 Generate Story", type="primary")

    if submit_btn:
        run_generation(topic, genre, story_type, reference_story, series_name, worldbook_path, selected_features, length_instruction, want_tts, debug_mode)

def run_generation(topic, genre, story_type, reference_story, series_name, worldbook_path, features, length_instruction, want_tts, debug_mode):
    base_prompt = read_base_prompt()
    voice_instruction = build_voice_instruction()
    story_context = load_story_context(reference_story)
    worldbook_context = load_worldbook_context(worldbook_path)

    status_ph = st.empty()
    prog_bar = st.progress(0)

    if debug_mode:
        story = TEST_STORY
        title = "Debug Test Story"
        status_ph.success("Debug mode: Loaded test story.")
    else:
        # Phase 1: Outline
        status_ph.info("Phase 1: Generating Outline...")
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
                        status_ph.info(f"Phase 1: Generating Outline... {token_count} tokens")
        
        status_ph.success(f"Phase 1: Outline Complete ({token_count} tokens)")
        with st.expander("View Outline"):
            st.write(outline)

        # Extract chapters
        chapter_matches = re.findall(r'(?:Chapter|chapter)\s+(\d+)', outline, re.IGNORECASE)
        total_chapters = max([int(x) for x in chapter_matches]) if chapter_matches else 10
        st.info(f"Detected {total_chapters} chapters")

        # Phase 2: Write Story
        status_ph.info(f"Phase 2: Writing Story ({total_chapters} Chapters)...")
        story_parts = []
        
        for chapter_num in range(1, total_chapters + 1):
            if chapter_num == 1:
                ch_prompt = f"""{base_prompt}
{worldbook_context}{story_context}{voice_instruction}
Based on this outline:
{outline}
Write Chapter {chapter_num} in detail. Wrap ALL dialogue in voice tags as described in the voice instructions above."""
            else:
                prev_content = ' '.join(story_parts[-1:])
                ch_prompt = f"""{base_prompt}
{worldbook_context}{story_context}{voice_instruction}
Continue the story from:
{prev_content}
Write Chapter {chapter_num} in detail. Wrap ALL dialogue in voice tags as described in the voice instructions above."""
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
                        if ch_tokens % 15 == 0:
                            status_ph.info(f"Phase 2: Writing Chapter {chapter_num}/{total_chapters}... {ch_tokens} tokens")
            
            story_parts.append(chapter)
            prog_bar.progress(chapter_num / total_chapters)
            status_ph.success(f"Chapter {chapter_num}/{total_chapters} Completed ({ch_tokens} tokens)")
        
        story = "\n\n".join(story_parts)

        # Phase 3: Title
        status_ph.info("Phase 3: Generating Title...")
        try:
            title_prompt = f"Based on the following story outline, create ONE compelling title:\n========\n{outline}\n========\nONLY OUTPUT THE TITLE, NOTHING ELSE!"
            title_response = llm_client.chat.completions.create(
                model=TITLE_MODEL, messages=[{"role": "user", "content": title_prompt}],
                max_tokens=30, temperature=0.7
            )
            title = title_response.choices[0].message.content.strip().replace('\n', ' ')[:50]
        except:
            title = "Untitled-Story"
        status_ph.success(f"Generated Title: {title}")

    # Save Files
    filepath, story_dir = save_story(story, title, series_name)
    tts_filepath = save_tts_story(story, title, story_dir)
    voices_used = extract_voices_used(story)
    save_metadata(title, story_type, reference_story, worldbook_path, features, story_dir, voices_used)
    
    st.subheader(f"📖 {title}")
    clean_story = remove_voice_tags(story)
    st.text_area("Story Content", clean_story, height=400)
    
    with open(filepath, "r") as f:
        st.download_button("Download TXT", f, file_name=f"{title}.txt")
    with open(story_dir / f"{sanitize_title(title)}.pdf", "rb") as f:
        st.download_button("Download PDF", f, file_name=f"{title}.pdf")

    # TTS
    if want_tts:
        st.subheader("🎙️ Generating TTS Audiobook...")
        generate_tts_from_text(story, title, story_dir, status_ph, prog_bar)

    st.balloons()
    st.success(f"🎉 Process completed successfully! Files saved to: {story_dir}")

def generate_tts_from_text(story_text, title, story_dir, status_ph, prog_bar):
    safe_title = sanitize_title(title)
    tts_dir = story_dir / f"{safe_title}_tts_segments"
    tts_dir.mkdir(parents=True, exist_ok=True)
    
    has_voice_tags = bool(VOICE_PATTERN.search(story_text))
    segments = parse_tts_text(story_text) if has_voice_tags else [{'voice': 'af_heart', 'text': p} for p in split_into_paragraphs(story_text)]
    
    audio_files = []
    total_segments = len(segments)
    
    for i, segment in enumerate(segments):
        if not segment['text'].strip():
            continue
        voice = segment['voice'] if segment['voice'] in VALID_VOICES else "af_heart"
        sentences = [s.strip() for s in re.split(r'[.!?]+', segment['text']) if s.strip()]
        
        for j, sentence in enumerate(sentences):
            try:
                with tts_client.audio.speech.with_streaming_response.create(
                    model="kokoro", voice=voice, input=sentence
                ) as response:
                    audio_file = tts_dir / f"segment_{i:03d}_{j:02d}_{voice}.mp3"
                    response.stream_to_file(str(audio_file))
                    audio_files.append(str(audio_file))
            except Exception as e:
                status_ph.error(f"TTS Error: {e}")
        
        prog_bar.progress((i + 1) / total_segments)
        status_ph.info(f"TTS Generation: {((i+1)/total_segments*100):.1f}% [{voice}]")
    
    # Combine
    status_ph.info("Fusing audio segments...")
    combined = AudioSegment.empty()
    for audio_file in audio_files:
        combined += AudioSegment.from_mp3(audio_file)
    
    audiobook_path = story_dir / f"{safe_title}_audiobook.mp3"
    combined.export(str(audiobook_path), format="mp3")
    
    try:
        shutil.rmtree(tts_dir)
    except:
        pass
    
    status_ph.success(f"Audiobook saved!")
    
    with open(audiobook_path, "rb") as f:
        st.download_button("Download Audiobook (MP3)", f, file_name=f"{title}_audiobook.mp3", mime="audio/mpeg")

def generate_tts_existing_page():
    st.header("Generate TTS for Existing Story")
    stories = get_all_stories()
    if not stories:
        st.warning("No stories found.")
        return
    
    story_opts = [str(s.relative_to(Path(OUTPUT_DIR).parent)) for s in stories]
    selected = st.selectbox("Select Story", story_opts)
    
    if st.button("Generate TTS", type="primary"):
        selected_file = next(s for s in stories if str(s.relative_to(Path(OUTPUT_DIR).parent)) == selected)
        tts_path = selected_file.parent / f"{selected_file.stem}_tts.txt"
        
        if tts_path.exists():
            with open(tts_path, 'r') as f:
                story_content = f.read()
            st.info("Using tagged version for TTS.")
        else:
            with open(selected_file, 'r') as f:
                story_content = f.read()
            st.info("No tagged version found, using clean version.")
        
        status_ph = st.empty()
        prog_bar = st.progress(0)
        generate_tts_from_text(story_content, selected_file.stem, selected_file.parent, status_ph, prog_bar)

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
    
    # Check for audiobook
    audiobook_path = selected_file.parent / f"{selected_file.stem}_audiobook.mp3"
    if audiobook_path.exists():
        st.subheader("🎙️ Audiobook")
        st.audio(str(audiobook_path))
        
        with open(audiobook_path, "rb") as f:
            st.download_button("Download Audiobook", f, file_name=f"{selected_file.stem}_audiobook.mp3", mime="audio/mpeg")

def worldbook_manager_page():
    st.header("🌍 Worldbook Manager")
    
    tab1, tab2 = st.tabs(["Create New", "Edit Existing"])
    
    with tab1:
        st.subheader("Create New Worldbook")
        name = st.text_input("Worldbook Name (filename)", key="new_wb_name")
        content = st.text_area("Worldbook Content", height=300, key="new_wb_content")
        
        if st.button("Save Worldbook", type="primary", key="save_new_wb"):
            if name and content:
                worldbook_path = Path(WORLDBOOK_DIR) / f"{name}.txt"
                with open(worldbook_path, 'w') as f:
                    f.write(content)
                st.success(f"Worldbook saved: {worldbook_path}")
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
        
        edited_content = st.text_area("Edit Content", current_content, height=300, key="edit_wb_content")
        
        if st.button("Update Worldbook", type="primary", key="update_wb"):
            with open(wb_path, 'w') as f:
                f.write(edited_content)
            st.success(f"Worldbook updated: {wb_path}")

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
    stories = get_all_stories()
    if not stories:
        st.warning("No stories found.")
        return
    
    story_opts = [str(s.relative_to(Path(OUTPUT_DIR).parent)) for s in stories]
    selected = st.selectbox("Select Story to Clean", story_opts)
    
    if st.button("Clean Story", type="primary"):
        selected_file = next(s for s in stories if str(s.relative_to(Path(OUTPUT_DIR).parent)) == selected)
        with open(selected_file, 'r') as f:
            story_content = f.read()
        
        clean_content = remove_voice_tags(story_content)
        clean_filepath = selected_file.parent / f"{selected_file.stem}_cleaned{selected_file.suffix}"
        
        with open(clean_filepath, 'w') as f:
            f.write(clean_content)
        
        st.success(f"Cleaned story saved: {clean_filepath}")
        st.text_area("Cleaned Content Preview", clean_content, height=300)

if __name__ == "__main__":
    main()