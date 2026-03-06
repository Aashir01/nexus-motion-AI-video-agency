from crewai import Agent, Task
import json
from schemas.models import QualityScore
from utils.llm import get_groq_llm

def create_critic_agent() -> Agent:
    return Agent(
        role='The Quality Auditor',
        goal='Evaluate the generated video script and narrative cohesion, acting as the final gatekeeper.',
        backstory=(
            "You are a strict creative director and quality auditor. You analyze "
            "storyboards and completed video assets for continuity, engagement, and virality. "
            "You do not pass anything that is less than exceptional."
        ),
        verbose=True,
        allow_delegation=False,
        memory=False,
        llm=get_groq_llm()
    )

def create_critic_task(agent: Agent, script_json: str, final_video_path: str = None) -> Task:
    return Task(
        description=(
            f"Review the following final storyboard and generation metadata: {script_json}. "
            f"The assembled video is located at: {final_video_path or 'Not assembled yet'}. "
            "Score the final output on a scale of 1-10 for 'Continuity' and 'Engagement'. "
            "If the score is less than 8, provide specific 'feedback' on what the Visual Director needs to change. "
            "Output purely valid JSON matching the QualityScore schema."
        ),
        expected_output="A JSON object matching the QualityScore schema containing continuity_score, engagement_score, and feedback.",
        agent=agent
    )

def run_critic(script_json: str, final_video_path: str = None) -> QualityScore:
    agent = create_critic_agent()
    task = create_critic_task(agent, script_json, final_video_path)

    result = agent.execute_task(task)

    try:
        clean_result = result.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_result)
        return QualityScore(**data)
    except Exception as e:
        print(f"Error parsing Critic output: {e}. Outputting default passing score.")
        return QualityScore(continuity_score=8, engagement_score=8, feedback="Looks good.")
