from langgraph.graph import StateGraph, END
from schemas.models import AgencyState
from agents.scout import run_scout
from agents.writer import run_writer
from agents.designer import run_designer
from agents.editor import run_editor_pipeline
from agents.critic import run_critic
from tools.assembly import assemble_final_video

def strategize_node(state: AgencyState):
    print("--- [Node] Strategizing ---")
    strategy = run_scout(state.niche_query)
    state.strategy = strategy
    return state

def write_node(state: AgencyState):
    print("--- [Node] Writing Script ---")
    script = run_writer(state.strategy)
    state.script = script
    return state

def design_node(state: AgencyState):
    print("--- [Node] Designing Visuals ---")
    enhanced_script = run_designer(state.niche_query, state.script)
    state.script = enhanced_script

    # Extract the seed created for the first scene
    if enhanced_script.scenes and enhanced_script.scenes[0].seed_id:
        state.base_character_seed = enhanced_script.scenes[0].seed_id

    return state

def synthesize_media_node(state: AgencyState):
    print("--- [Node] Synthesizing Media ---")
    populated_script = run_editor_pipeline(state.script)
    state.script = populated_script
    return state

def assemble_node(state: AgencyState):
    print("--- [Node] Assembling Video ---")
    final_path = assemble_final_video(state.script)
    state.final_video_path = final_path
    return state

def review_node(state: AgencyState):
    print("--- [Node] Quality Audit ---")
    script_json = state.script.model_dump_json() if state.script else "{}"
    score = run_critic(script_json, state.final_video_path)
    state.score = score
    state.refinement_iterations += 1

    print(f"Critics Score: Continuity={score.continuity_score}, Engagement={score.engagement_score}")
    print(f"Feedback: {score.feedback}")
    return state

def should_refine(state: AgencyState) -> str:
    print("--- [Router] Analyzing Quality Score ---")
    if not state.score:
        return "end"

    avg_score = (state.score.continuity_score + state.score.engagement_score) / 2

    if avg_score < 8.0 and state.refinement_iterations < 2:
        print("Score below 8. Routing to Visual Director for Refinement.")
        return "refine"
    else:
        print("Score passing or max iterations reached. Workflow Complete.")
        return "end"

def build_workflow() -> StateGraph:
    workflow = StateGraph(AgencyState)

    # Add Nodes
    workflow.add_node("strategist", strategize_node)
    workflow.add_node("writer", write_node)
    workflow.add_node("designer", design_node)
    workflow.add_node("media_synth", synthesize_media_node)
    workflow.add_node("assembler", assemble_node)
    workflow.add_node("critic", review_node)

    # Add Edges
    workflow.set_entry_point("strategist")
    workflow.add_edge("strategist", "writer")
    workflow.add_edge("writer", "designer")

    # In a refinement loop, the Designer can take the critic's feedback
    workflow.add_edge("designer", "media_synth")
    workflow.add_edge("media_synth", "assembler")
    workflow.add_edge("assembler", "critic")

    # Conditional workflow based on Critic's score
    workflow.add_conditional_edges(
        "critic",
        should_refine,
        {
            "refine": "designer",
            "end": END
        }
    )

    return workflow.compile()
