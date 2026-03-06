import './PipelineVisualizer.css'

export default function PipelineVisualizer({ steps, activeStep, completedSteps, phase }) {
    return (
        <div className="pipeline-wrap">
            <div className="pipeline-track">
                {steps.map((step, i) => {
                    const isActive = activeStep === i
                    const isDone = completedSteps.includes(i)
                    const isIdle = !isActive && !isDone

                    return (
                        <div key={step.id} className="pipeline-item">
                            {/* Connector line */}
                            {i < steps.length - 1 && (
                                <div className={`connector ${isDone || isActive ? 'connector--lit' : ''}`}>
                                    <div className={`connector-particle ${isActive ? 'connector-particle--flow' : ''}`} />
                                </div>
                            )}
                            {/* Node */}
                            <div className={`pipeline-node
                ${isActive ? 'pipeline-node--active' : ''}
                ${isDone ? 'pipeline-node--done' : ''}
                ${isIdle ? 'pipeline-node--idle' : ''}
              `}
                                style={{ '--node-color': step.color }}
                            >
                                {isActive && <div className="node-ping" />}
                                <div className="node-icon">{isDone ? '✓' : step.icon}</div>
                            </div>
                            <div className={`node-label ${isActive ? 'node-label--active' : ''}`}>
                                {step.label}
                            </div>
                        </div>
                    )
                })}
            </div>

            {/* Refinement loop indicator */}
            {phase !== 'idle' && (
                <div className="refinement-hint">
                    <svg width="100%" height="30" viewBox="0 0 600 30" className="refinement-svg">
                        <path
                            d="M 430 10 Q 515 -10 515 15 Q 515 40 430 30"
                            stroke="rgba(168,85,247,0.4)"
                            strokeWidth="1.5"
                            fill="none"
                            strokeDasharray="4 3"
                        />
                        <polygon points="430,26 424,32 436,32" fill="rgba(168,85,247,0.5)" />
                    </svg>
                    <span className="refinement-label">Refinement loop (if score &lt; 8)</span>
                </div>
            )}
        </div>
    )
}
