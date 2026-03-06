import traceback
import sys
from dotenv import load_dotenv
load_dotenv()
from workflows.graph import build_workflow
from schemas.models import AgencyState

app = build_workflow()
state = AgencyState(niche_query='test', refinement_iterations=0)
try:
    app.invoke(state)
except Exception as e:
    with open('error_log.txt', 'w') as f:
        traceback.print_exc(file=f)
