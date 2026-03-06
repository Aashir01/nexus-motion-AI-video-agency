import os
import argparse
from dotenv import load_dotenv

load_dotenv()

from schemas.models import AgencyState
from workflows.graph import build_workflow

def main():
    parser = argparse.ArgumentParser(description="Nexus-Motion MAS Agency")
    parser.add_argument("--niche", type=str, required=True, help="The niche and hook idea for the video")
    args = parser.parse_args()

    niche_query = args.niche
    print(f"\\n{'='*50}")
    print(f"🚀 INITIALIZING NEXUS-MOTION AGENCY")
    print(f"🎯 Target Niche/Hook: {niche_query}")
    print(f"{'='*50}\\n")

    # Display environment warnings
    if not os.getenv("GROQ_API_KEY") or os.getenv("GROQ_API_KEY") == "your_groq_api_key_here":
        print("❌ CRITICAL: GROQ_API_KEY is not set. The agents will fail to run.")
        print("Please configure your .env file.\\n")
        return

    # Initialize Graph
    try:
        app = build_workflow()
    except Exception as e:
        print(f"❌ Failed to build workflow graph: {e}")
        return

    # Initialize State
    initial_state = AgencyState(
        niche_query=niche_query,
        refinement_iterations=0
    )

    print("🤖 Agency Pipeline Active.\\n")
    
    try:
        # Run the compiled graph
        final_state = app.invoke(initial_state)
        
        print(f"\\n{'='*50}")
        print("✨ AGENCY WORKFLOW COMPLETE ✨")
        print(f"🎬 Final Video Output: {final_state.get('final_video_path', 'Not generated')}")
        
        score = final_state.get('score')
        if score:
            print(f"📊 Final Quality Score: Continuity={score.continuity_score}, Engagement={score.engagement_score}")
            print(f"📝 Critic Feedback: {score.feedback}")
        print(f"{'='*50}\\n")
        
    except Exception as e:
        print(f"\\n❌ Agency Pipeline Failed: {e}")

if __name__ == "__main__":
    main()
