from crewai import Agent, Task
import json
from schemas.models import StrategyReport, Script
from utils.llm import get_groq_llm

def create_writer_agent() -> Agent:
    return Agent(
        role='The Script Architect',
        goal='Expand a retention strategy into a highly engaging, 12-scene JSON script for a 50-60 second video.',
        backstory=(
            "You are a viral content scriptwriter. You know how to pace a video perfectly, "
            "ensuring every 4-5 seconds contains a visual change or new information byte. "
            "You are meticulous about structure and formatting."
        ),
        verbose=True,
        allow_delegation=False,
        memory=False,
        llm=get_groq_llm()
    )

def create_writer_task(agent: Agent, strategy: StrategyReport) -> Task:
    return Task(
        description=(
            f"Given this strategy: Hook: {strategy.hook_strategy}, Body: {strategy.body_strategy}, CTA: {strategy.cta_strategy}. "
            "Format a complete script exactly 12 scenes long. Each scene shouldn't exceed 5 seconds of pacing. "
            "For each scene, output: scene_number, timestamp, dialogue, visual_description, camera_instruction. "
            "Output purely valid JSON containing a 'scenes' array."
        ),
        expected_output="A JSON object matching the Script schema with exactly 12 scenes.",
        agent=agent
    )

def run_writer(strategy: StrategyReport) -> Script:
    agent = create_writer_agent()
    task = create_writer_task(agent, strategy)

    result = agent.execute_task(task)

    try:
        clean_result = result.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_result)
        return Script(**data)
    except Exception as e:
        print(f"Error parsing Writer output: {e}. Outputting default fallback.")
        raise ValueError("Writer failed to produce valid Script JSON.")
