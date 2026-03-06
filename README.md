# Nexus-Motion Autonomous Video Agency (Free Edition)

Nexus-Motion is a production-grade Multi-Agent System (MAS) that transforms a single "Niche + Hook" idea into a fully scripted, designed, and assembled 30-60 second short-form video.

This version is the **Free Portfolio Edition**, engineered with a strict zero-cost constraint:
- **Intelligence:** Groq (Llama 3 70B) for instant, free agent reasoning.
- **Persistence:** Aiven (Valkey/Redis) free-tier for character aesthetic consistency.
- **Video Synth:** Gradio Client targeting free HunyuanVideo/Wan spaces (with a MockMedia local fallback).
- **Audio Synth:** Google TTS (gTTS) for free voiceovers, with ElevenLabs premium overrides available.

## Architecture

Built using:
- **CrewAI** for role-specific AI Agents (Scout, Writer, Designer, Editor, Critic).
- **LangGraph** for stateful loop orchestration and quality refinement.
- **MoviePy** for automated video assembly and rendering.

## Setup Instructions (Windows)

1. **Install System Dependencies**
   MoviePy requires FFmpeg and ImageMagick to process videos.
   - Install FFmpeg: `winget install ffmpeg`
   - Install ImageMagick: `winget install ImageMagick.ImageMagick`

2. **Install Python Libraries**
   Ensure you are in the `nexus_motion` directory.
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**
   Rename `.env.example` to `.env` and add your required keys:
   - `GROQ_API_KEY`: Get a free key from [console.groq.com](https://console.groq.com/).
   - `REDIS_URI`: (Optional but recommended) Get a free Valkey/Redis URI from [Aiven](https://aiven.io/). If left blank, it uses local memory.

## Usage

Run the main pipeline by specifying a niche and hook idea. The agents will automatically research, write, prompt, generate, and assemble the video. 

```bash
python main.py --niche "AI Productivity Hacks for Students"
```

### Fallback System
If the public Video Generation Spaces (Gradio) are too busy or fail, the `Editor Agent` will automatically route to `MockMedia` and generate 1-second local local mock files so the pipeline can continue to MoviePy assembly without crashing.
