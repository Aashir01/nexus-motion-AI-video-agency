import { useState, useRef, useEffect, useCallback } from 'react'
import HeroInput from './components/HeroInput.jsx'
import PipelineVisualizer from './components/PipelineVisualizer.jsx'
import LogTerminal from './components/LogTerminal.jsx'
import ResultsDashboard from './components/ResultsDashboard.jsx'
import VideoPlayer from './components/VideoPlayer.jsx'
import './App.css'

const PIPELINE_STEPS = [
    { id: 'strategist', label: 'Niche\nStrategist', icon: '🔍', color: '#00d4ff', keywords: ['Strategizing', 'Niche Strategist', 'Scout'] },
    { id: 'writer', label: 'Script\nArchitect', icon: '✍️', color: '#a855f7', keywords: ['Writing Script', 'Script Architect', 'Writer'] },
    { id: 'designer', label: 'Visual\nDirector', icon: '🎨', color: '#f59e0b', keywords: ['Designing Visuals', 'Visual Director', 'Designer'] },
    { id: 'media_synth', label: 'Media\nSynth', icon: '🎬', color: '#10b981', keywords: ['Synthesizing Media', 'Media Synth', 'Editor'] },
    { id: 'assembler', label: 'Video\nAssembler', icon: '⚙️', color: '#f43f5e', keywords: ['Assembling Video', 'Assembler'] },
    { id: 'critic', label: 'Quality\nAuditor', icon: '⭐', color: '#06b6d4', keywords: ['Quality Audit', 'Critic', 'Score'] },
]

function detectActiveStep(log) {
    const upper = log.toUpperCase()
    for (let i = 0; i < PIPELINE_STEPS.length; i++) {
        if (PIPELINE_STEPS[i].keywords.some(kw => upper.includes(kw.toUpperCase()))) {
            return i
        }
    }
    return -1
}

export default function App() {
    const [phase, setPhase] = useState('idle') // idle | running | done | error
    const [jobId, setJobId] = useState(null)
    const [logs, setLogs] = useState([])
    const [activeStep, setActiveStep] = useState(-1)
    const [completedSteps, setCompletedSteps] = useState([])
    const [result, setResult] = useState(null)
    const [error, setError] = useState(null)
    const esRef = useRef(null)

    const startJob = useCallback(async (nicheQuery) => {
        setPhase('running')
        setLogs([])
        setActiveStep(0)
        setCompletedSteps([])
        setResult(null)
        setError(null)

        try {
            const res = await fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ niche_query: nicheQuery })
            })
            if (!res.ok) {
                const data = await res.json()
                throw new Error(data.detail || 'Failed to start job')
            }
            const data = await res.json()
            setJobId(data.job_id)
            connectSSE(data.job_id)
        } catch (e) {
            setError(e.message)
            setPhase('error')
        }
    }, [])

    function connectSSE(id) {
        if (esRef.current) esRef.current.close()
        const es = new EventSource(`/api/stream/${id}`)
        esRef.current = es
        let currentStep = 0

        es.onmessage = (evt) => {
            const msg = evt.data
            if (msg === '__DONE__') {
                es.close()
                setActiveStep(-1)
                setCompletedSteps(PIPELINE_STEPS.map((_, i) => i))
                setPhase('done')
                fetchResult(id)
                return
            }
            if (msg === '__ERROR__') {
                es.close()
                setPhase('error')
                setError('Pipeline failed. Check logs for details.')
                return
            }
            setLogs(prev => [...prev, msg])

            const detected = detectActiveStep(msg)
            if (detected > -1 && detected > currentStep) {
                if (currentStep >= 0) {
                    setCompletedSteps(prev => [...new Set([...prev, currentStep])])
                }
                currentStep = detected
                setActiveStep(detected)
            }
        }

        es.onerror = () => {
            es.close()
            setPhase('error')
            setError('Connection to server lost. Is the API running on port 8000?')
        }
    }

    async function fetchResult(id) {
        try {
            const res = await fetch(`/api/result/${id}`)
            if (res.ok) {
                const data = await res.json()
                setResult(data)
            }
        } catch (e) {
            console.error('Failed to fetch result:', e)
        }
    }

    useEffect(() => {
        return () => { if (esRef.current) esRef.current.close() }
    }, [])

    return (
        <div className="app-layout">
            {/* Header */}
            <header className="app-header">
                <div className="header-logo">
                    <div className="logo-orb" />
                    <span className="logo-text">NEXUS<span className="logo-accent">·MOTION</span></span>
                </div>
                <div className="header-badge">
                    <span className="badge-dot" /> AI Video Agency
                </div>
            </header>

            {/* Hero */}
            <HeroInput onSubmit={startJob} isRunning={phase === 'running'} />

            {/* Pipeline (shown once running/done) */}
            {phase !== 'idle' && (
                <section className="section fade-in-section">
                    <div className="section-label">PIPELINE</div>
                    <PipelineVisualizer
                        steps={PIPELINE_STEPS}
                        activeStep={activeStep}
                        completedSteps={completedSteps}
                        phase={phase}
                    />
                </section>
            )}

            {/* Log Terminal */}
            {logs.length > 0 && (
                <section className="section fade-in-section">
                    <div className="section-label">LIVE LOGS</div>
                    <LogTerminal logs={logs} phase={phase} />
                </section>
            )}

            {/* Error Banner */}
            {phase === 'error' && error && (
                <div className="error-banner fade-in-section">
                    <span style={{ fontSize: '1.2rem' }}>❌</span>
                    <span>{error}</span>
                </div>
            )}

            {/* Results */}
            {phase === 'done' && result && (
                <section className="section fade-in-section">
                    <div className="section-label">RESULTS</div>
                    <ResultsDashboard result={result} jobId={jobId} />
                </section>
            )}

            {/* Video Player */}
            {phase === 'done' && result?.final_video_path && (
                <section className="section fade-in-section">
                    <div className="section-label">FINAL REEL</div>
                    <VideoPlayer jobId={jobId} />
                </section>
            )}

            <footer className="app-footer">
                <span>Nexus-Motion MAS · Powered by LangGraph + CrewAI + Groq</span>
            </footer>
        </div>
    )
}
