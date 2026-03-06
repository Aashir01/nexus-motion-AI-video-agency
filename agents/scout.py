from crewai import Agent, Task
import json
from schemas.models import StrategyReport
from utils.llm import get_groq_llm

def create_scout_agent() -> Agent:
    return Agent(
        role='The Niche Strategist',
        goal='Analyze the user specified niche and develop a high-retention video strategy.',
        backstory=(
            "You are an expert market analyst and trend researcher for Instagram Reels and TikTok. "
            "You understand human psychology, pattern interrupts, and what makes a viewer stop scrolling."
        ),
        verbose=True,
        allow_delegation=False,
        memory=False,
        llm=get_groq_llm()
    )

def create_scout_task(agent: Agent, niche_query: str) -> Task:
    return Task(
        description=(
            f"Analyze the following niche/hook idea: '{niche_query}'. "
            "Develop a 'Retention Strategy' consisting of: "
            "1. Hook Strategy: A visual and verbal pattern interrupt to grab attention in the first 3 seconds. "
            "2. Body Strategy: How to deliver value and build tension or curiosity. "
            "3. CTA Strategy: An engaging call to action to maximize shares and saves. "
            "Output your strategy in pure JSON format matching the StrategyReport schema."
        ),
        expected_output="A JSON object matching the StrategyReport schema containing hook, body, and CTA strategies.",
        agent=agent
    )

def run_scout(niche_query: str) -> StrategyReport:
    agent = create_scout_agent()
    task = create_scout_task(agent, niche_query)

    result = agent.execute_task(task)

    try:
        clean_result = result.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_result)
        return StrategyReport(**data)
    except Exception as e:
        print(f"Error parsing Scout output: {e}. Raw output: {result}")
        return StrategyReport(
            hook_strategy="Rapid zoom, controversial statement.",
            body_strategy="Fast paced facts.",
            cta_strategy="Save this reel."
        )
