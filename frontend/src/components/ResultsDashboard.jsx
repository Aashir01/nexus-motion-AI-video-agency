import { useState } from 'react'
import './ResultsDashboard.css'

function ScoreGauge({ label, value, color }) {
    const pct = Math.round((value / 10) * 100)
    const circumference = 2 * Math.PI * 36
    const strokeDashoffset = circumference - (pct / 100) * circumference

    return (
        <div className="gauge-wrap">
            <svg className="gauge-svg" viewBox="0 0 88 88" width="88" height="88">
                <circle cx="44" cy="44" r="36" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="6" />
                <circle
                    cx="44" cy="44" r="36" fill="none"
                    stroke={color} strokeWidth="6"
                    strokeLinecap="round"
                    strokeDasharray={circumference}
                    strokeDashoffset={strokeDashoffset}
                    transform="rotate(-90 44 44)"
                    style={{ transition: 'stroke-dashoffset 1.2s ease', filter: `drop-shadow(0 0 6px ${color})` }}
                />
                <text x="44" y="50" textAnchor="middle" fontSize="18" fontWeight="800" fill={color} fontFamily="Inter">
                    {value}
                </text>
            </svg>
            <span className="gauge-label">{label}</span>
        </div>
    )
}

function StrategyCard({ strategy }) {
    return (
        <div className="strategy-grid">
            {[
                { key: 'hook_strategy', label: '🎣 Hook Strategy', color: '#00d4ff' },
                { key: 'body_strategy', label: '💡 Body Strategy', color: '#a855f7' },
                { key: 'cta_strategy', label: '📣 CTA Strategy', color: '#10b981' },
            ].map(({ key, label, color }) => (
                <div key={key} className="strategy-card glass" style={{ '--card-color': color }}>
                    <div className="strategy-card-label">{label}</div>
                    <p className="strategy-card-text">{strategy[key]}</p>
                </div>
            ))}
        </div>
    )
}

function SceneAccordion({ scenes }) {
    const [openIdx, setOpenIdx] = useState(null)

    return (
        <div className="scenes-list">
            {scenes.map((scene, i) => (
                <div key={i} className={`scene-item ${openIdx === i ? 'scene-item--open' : ''}`}>
                    <button
                        className="scene-header"
                        onClick={() => setOpenIdx(openIdx === i ? null : i)}
                    >
                        <div className="scene-number">
                            <span>{String(scene.scene_number).padStart(2, '0')}</span>
                        </div>
                        <div className="scene-meta">
                            <span className="scene-timestamp">{scene.timestamp}</span>
                            <span className="scene-dialogue-preview">
                                {scene.dialogue?.slice(0, 70)}{scene.dialogue?.length > 70 ? '…' : ''}
                            </span>
                        </div>
                        <div className={`scene-chevron ${openIdx === i ? 'scene-chevron--open' : ''}`}>▾</div>
                    </button>
                    {openIdx === i && (
                        <div className="scene-body">
                            <div className="scene-field">
                                <div className="scene-field-label">🗣 Dialogue</div>
                                <p>{scene.dialogue}</p>
                            </div>
                            <div className="scene-field">
                                <div className="scene-field-label">🎬 Visual Description</div>
                                <p>{scene.visual_description}</p>
                            </div>
                            <div className="scene-field">
                                <div className="scene-field-label">📷 Camera</div>
                                <p>{scene.camera_instruction}</p>
                            </div>
                            {scene.video_prompt && (
                                <div className="scene-field">
                                    <div className="scene-field-label">🤖 AI Video Prompt</div>
                                    <p className="scene-prompt">{scene.video_prompt}</p>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            ))}
        </div>
    )
}

export default function ResultsDashboard({ result, jobId }) {
    const { strategy, script, score } = result

    return (
        <div className="results-wrap">
            {/* Quality Score */}
            {score && (
                <div className="results-block">
                    <div className="results-block-title">Quality Score</div>
                    <div className="scores-row">
                        <ScoreGauge label="Continuity" value={score.continuity_score} color="#00d4ff" />
                        <ScoreGauge label="Engagement" value={score.engagement_score} color="#a855f7" />
                        <div className="score-avg">
                            <span className="score-avg-num">
                                {((score.continuity_score + score.engagement_score) / 2).toFixed(1)}
                            </span>
                            <span className="score-avg-label">avg / 10</span>
                        </div>
                        {score.feedback && (
                            <div className="score-feedback glass">
                                <div className="score-feedback-label">Critic Feedback</div>
                                <p>{score.feedback}</p>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Strategy */}
            {strategy && (
                <div className="results-block">
                    <div className="results-block-title">Retention Strategy</div>
                    <StrategyCard strategy={strategy} />
                </div>
            )}

            {/* Script */}
            {script?.scenes?.length > 0 && (
                <div className="results-block">
                    <div className="results-block-title">
                        Script — {script.scenes.length} Scenes
                        <span className="scenes-badge">{script.scenes.length}/12</span>
                    </div>
                    <SceneAccordion scenes={script.scenes} />
                </div>
            )}
        </div>
    )
}
