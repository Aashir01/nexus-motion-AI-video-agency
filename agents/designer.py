from crewai import Agent, Task
import json
import uuid
from typing import Dict, Any
from schemas.models import Script, Scene
from utils.llm import get_groq_llm
from memory.state import brand_db

def create_designer_agent() -> Agent:
    return Agent(
        role='The Visual Director',
        goal='Convert visual descriptions into hyper-detailed cinematic prompts and manage Character Seed IDs for visual consistency.',
        backstory=(
            "You are an award-winning cinematic prompt engineer for AI video models like Kling and Luma. "
            "You know how to write 70-100 word prompts describing lighting, camera lenses, subject placement, and motion. "
            "You always maintain character consistency across scenes."
        ),
        verbose=True,
        allow_delegation=False,
        memory=False,
        llm=get_groq_llm()
    )

def create_designer_task(agent: Agent, script_json: str, base_seed: str = None) -> Task:
    return Task(
        description=(
            f"Enhance the following script: {script_json}. "
            "For each scene, create a 'video_prompt' (70-100 words) using the 'visual_description' and 'camera_instruction'. "
            "If a 'base_seed' is provided, assign it to the 'seed_id' of EVERY scene. "
            "If 'base_seed' is None, generate a random 10-digit numeric string for Scene 1's 'seed_id', and reuse it for all subsequent scenes. "
            f"Base Seed provided: {base_seed}. "
            "Output purely valid JSON containing the updated 'scenes' array matching the Script schema."
        ),
        expected_output="A JSON object matching the Script schema with updated 'video_prompt' and 'seed_id' fields for all 12 scenes.",
        agent=agent
    )

def run_designer(niche_query: str, script: Script) -> Script:
    # Check if we already have a seed for this niche in the Brand Database
    existing_seed = brand_db.get_seed(niche_query)

    agent = create_designer_agent()
    script_json = script.model_dump_json()
    task = create_designer_task(agent, script_json, base_seed=existing_seed)

    result = agent.execute_task(task)

    try:
        clean_result = result.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_result)
        enhanced_script = Script(**data)

        # Save the seed for future runs to maintain the brand aesthetic
        if not existing_seed and enhanced_script.scenes:
            first_scene_seed = enhanced_script.scenes[0].seed_id
            if first_scene_seed:
                brand_db.set_seed(niche_query, first_scene_seed)

        return enhanced_script
    except Exception as e:
        print(f"Error parsing Designer output: {e}. Returning original script.")
        return script
