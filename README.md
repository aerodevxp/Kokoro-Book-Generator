
# 📖 Book/Novel Generator

Generate full-length novels with AI, complete with multi-voice TTS audiobooks, sound effects, cover art, and series management.

## 🚀 Quick Start

```bash
# Clone and setup
cp .env.example .env
nano .env  # Configure your API endpoints

# Run
chmod +x run_local.sh
./run_local.sh
```

Then open your browser to the Streamlit URL shown in the terminal.

## 📋 Requirements

- **Python 3.10+**
- **ffmpeg** installed and in your system PATH
- An **LLM API endpoint** (OpenAI-compatible, e.g. llama.cpp, vLLM, Ollama)
- A **Kokoro TTS endpoint** (OpenAI-compatible `/audio/speech` endpoint)
- An **Image generation API endpoint** (OpenAI-compatible `/images/generations`)
- Max 1GB of available RAM for TTS editing. (Only needed when starting a generation)

### Python Dependencies

```bash
pip install - requirements.txt
```

## ⚙️ Configuration (.env)

```env
# LLM Configuration
STORY_MODEL=your-story-model-name
TITLE_MODEL=your-title-model-name
BASE_URL=http://localhost:8080/v1
LLM_API_KEY=your-key-or-dummy

# TTS Configuration (Kokoro)
TTS_URL=http://localhost:8880/v1
TTS_API_KEY=not-needed

# Image Generation
IMG_MODEL=your-image-model-name
IMG_URL=http://localhost:7860/v1
IMG_API_KEY=not-needed

# Paths
BASE_PROMPT_PATH=./prompts/base_prompt.txt
OUTPUT_DIR=./books
SFX_DIR=./assets/sfx
```

## ✨ Features

### Story Generation
- **AI-driven outlining** — Generates a chapter-by-chapter outline before writing
- **Chapter-by-chapter writing** — Each chapter is written individually with streaming
- **Running chapter summaries** — After each chapter, a 150-word summary is generated and used as context for subsequent chapters, keeping the AI on track for long stories
- **Automatic book summaries** — Combines all chapter summaries into a 600-word book summary, saved for instant sequel generation
- **Quick Test mode** — Generates exactly 1 chapter to test the full pipeline
- **Debug mode** — Uses a built-in test story, skips AI generation entirely
- **Cancel & Retry** — Cancel running jobs or retry failed ones with the same parameters

### Multi-Voice TTS Audiobooks
- **Kokoro native multi-speaker support** — Uses `[voice:name]` tags for seamless speaker switching
- **50+ voices** across multiple languages and genders
- **Voice mixing** — Blend two voices with weighted ratios (e.g. `af_bella(2)+af_nova(1)`) to create unique character voices
- **Control tokens**:
  - `[pause:1.5s]` — Insert silence for dramatic effect
  - `[rate:0.8]` — Speed up or slow down speech
  - `[Worcester](/wˈʊstər/)` — IPA pronunciation overrides
- **Automatic audio fusion** — All voice clips are combined into a single MP3 audiobook with pauses between paragraphs
- **MP3 validation & retries** — Corrupt audio segments are automatically retried (up to 3 times)

### Sound Effects
- **Inline SFX** — `[sfx:door_creak]` inserts a sound effect that interrupts speech
- **Background SFX** — `[bgsfx:rain]` starts a looping background sound, `[/bgsfx]` stops it. Background sounds persist across multiple voice clips and paragraphs
- **Dynamic SFX discovery** — The script scans your `SFX_DIR` and tells the AI exactly which effects are available
- **Automatic volume leveling** — SFX and background sounds are automatically lowered so speech remains clear

### Cover Art
- **AI-generated cover images** based on the story title and summary
- **Embedded in MP3** as album art with title and artist metadata

### Series Management
- **Create and organize series** with dedicated folders
- **Sequels & prequels** — Reference existing stories; the AI receives the previous book's summary as context
- **Reading order tracking** — Stories are numbered within a series
- **Add existing stories to series** — Move standalone stories into a series at any time
- **Series renaming** — Rename series from the UI

### Worldbook System
- **Create and edit worldbooks** — Define world lore, locations, history, and character descriptions
- **Character voice assignments** — Add a `[CHARACTER VOICES]` section to your worldbook to assign specific voices to characters. The AI will use these exact voices and maintain consistency across all stories in a series
- **Worldbook-series linking** — Link worldbooks to series for automatic context injection
- **Standalone story support** — Worldbooks can be used with standalone stories too

### Story Library
- **Browse all generated stories** in one place
- **View story content** and book summaries
- **Generate missing summaries** for older stories with one click
- **Play and download audiobooks** directly from the library
- **Clean voice tags** from any story (removes all TTS markup for clean text export)

### Feature Manager
- **Customizable story features** — Edit the list of available features (Magic System, Political Intrigue, etc.) that appear as checkboxes during generation

### Background Processing
- **Threaded job execution** — All generation runs in background threads; you can navigate the UI freely
- **Live progress tracking** — Progress bars and status messages update in real-time
- **Job persistence** — Job status is saved to JSON files, surviving page refreshes
- **Automatic cleanup** — Old completed/failed jobs are automatically removed

## 📁 File Structure

```
project/
├── main.py
├── .env
├── prompts/
│   └── base_prompt.txt
├── assets/
│   ├── DejaVuSans.ttf          # Font for PDF generation
│   └── sfx/                    # Sound effects folder
│       ├── door_creak.mp3
│       ├── rain.mp3
│       ├── thunder.mp3
│       └── ...
└── books/                      # Output directory
    ├── StoryTitle/
    │   ├── StoryTitle.txt              # Clean story (no voice tags)
    │   ├── StoryTitle.pdf              # PDF version
    │   ├── StoryTitle_tts.txt          # Tagged version (with voice/SFX tags)
    │   ├── StoryTitle_audiobook.mp3    # Combined audiobook with cover art
    │   ├── StoryTitle_cover.png        # Cover image
    │   ├── StoryTitle_metadata.json    # Story metadata
    │   ├── StoryTitle_summary.txt      # 600-word book summary
    │   └── StoryTitle_chapter_summaries.json  # Per-chapter summaries
    ├── worldbooks/
    │   ├── MyWorld.txt
    │   └── MyWorld.meta.json
    ├── series/
    │   └── MySeries/
    │       ├── series.json
    │       └── StoryTitle/
    │           └── ... (same as above)
    ├── features.txt
    └── jobs/
        └── job_1234567890_status.json
```

## 🎙️ Voice Tag Format

The AI generates stories with voice tags for TTS. The clean version (TXT/PDF) has all tags stripped automatically.

```xml
<af_heart>The sun set over the mountains. [pause:0.5s] Long shadows stretched across the valley.</af_heart>
<af_bella>"I can't believe you did that!" [pause:1s] She shook her head in disbelief.</af_bella>
<am_adam>"It was the only way." [rate:0.8] [sfx:door_creak] His voice was barely a whisper.</am_adam>
```

## 🔊 Adding Sound Effects

1. Download free MP3 sound effects (e.g. from [freesound.org](https://freesound.org))
2. Place them in your `SFX_DIR` folder
3. Name them clearly (e.g. `door_creak.mp3`, `rain.mp3`, `thunder.mp3`)
4. The script will automatically detect them and tell the AI they're available

The AI will use `[sfx:name]` for interrupting effects and `[bgsfx:name]...[/bgsfx]` for background ambiance.

## 🌍 Worldbook Character Voices

Add a `[CHARACTER VOICES]` section at the end of your worldbook:

```
[CHARACTER VOICES]
John Doe: am_adam
Jane Smith: af_bella
The Narrator: af_heart
```

The AI will use these exact voices for these characters in every story that uses this worldbook.

## 📝 Base Prompt

Create a `base_prompt.txt` file at the path specified in your `.env`. This is the system prompt sent to the LLM before the outline generation. It should contain your general writing guidelines, style preferences, and any rules you want the AI to follow.


## 📜 License

Personal use. Do whatever you want with it.
