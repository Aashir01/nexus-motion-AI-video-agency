import os

def generate_audio(text: str, voice_override: str = None) -> str:
    """
    Zero-cost audio generation using Google TTS (gTTS) as a fallback,
    or ElevenLabs if the ELEVENLABS_API_KEY is present in the environment.
    """
    output_dir = "output/audio"
    os.makedirs(output_dir, exist_ok=True)

    file_path = f"{output_dir}/audio_scene_{hash(text)}.mp3"

    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")

    if elevenlabs_key and elevenlabs_key != "your_elevenlabs_api_key_here":
        try:
            print("Using Premium Audio (ElevenLabs)...")
            import requests
            url = "https://api.elevenlabs.io/v1/text-to-speech/pNInz6obpgDQGcFmaJgB"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": elevenlabs_key
            }
            data = {
                "text": text,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}
            }
            response = requests.post(url, json=data, headers=headers)
            if response.status_code == 200:
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                return file_path
            else:
                print(f"ElevenLabs API Error: {response.text}")
        except Exception as e:
            print(f"ElevenLabs failed: {e}. Falling back to Free TTS.")

    print("Using Free Audio (edge-tts)...")
    try:
        import subprocess
        import sys
        # en-US-ChristopherNeural is a highly realistic male AI voice
        subprocess.run([sys.executable, "-m", "edge_tts", "--text", text, "--write-media", file_path, "--voice", "en-US-ChristopherNeural"], check=True)
        return file_path
    except Exception as e:
        print(f"Critical Audio Failure (edge-tts): {e}")
        return None
