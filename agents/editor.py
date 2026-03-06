from crewai import Agent, Task
from schemas.models import Script
from utils.llm import get_groq_llm
import traceback

def create_editor_agent() -> Agent:
    return Agent(
        role='The Media Synth Agent',
        goal='Coordinate the video and audio generation tools, handling fallbacks to Mock/Open-Source modes seamlessly when limits are hit.',
        backstory=(
            "You are an automated pipeline integration specialist. You convert prompts into actual mp4 files. "
            "You are extremely resilient and have robust fallback logic. If a premium API runs out of free credits, "
            "you immediately switch to MockMedia or Gradio open-source spaces without skipping a beat."
        ),
        verbose=True,
        allow_delegation=False,
        memory=False,
        llm=get_groq_llm()
    )

def run_editor_pipeline(script: Script) -> Script:
    """
    The Media Synth Agent's execution pipeline.
    Iterates over scenes and calls the respective tools for Video and Audio generation.
    """
    try:
        from tools.video_engine import generate_video
        from tools.audio_engine import generate_audio
    except ImportError:
        print("Warning: Tools not fully implemented yet. Using dummy generation.")
        generate_video = lambda p, s: f"mock_video_{s}.mp4"
        generate_audio = lambda d: f"mock_audio.mp3"

    for i, scene in enumerate(script.scenes):
        print(f"[Editor Agent] Processing Scene {scene.scene_number} / 12")

        # 1. Generate Video
        try:
            video_path = generate_video(scene.video_prompt, scene.seed_id)
            scene.video_path = video_path
        except Exception as e:
            print(f"Failed to generate video for scene {scene.scene_number}: {e}")
            traceback.print_exc()
            scene.video_path = f"local_mock_video_fallback_{scene.scene_number}.mp4"

        # 2. Generate Audio
        try:
            audio_path = generate_audio(scene.dialogue)
            scene.audio_path = audio_path
        except Exception as e:
            print(f"Failed to generate audio for scene {scene.scene_number}: {e}")
            scene.audio_path = f"local_mock_audio_fallback_{scene.scene_number}.mp3"

    return script
