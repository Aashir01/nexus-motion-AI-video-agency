import { useRef, useEffect } from 'react'
import './LogTerminal.css'

// Color code log lines by which agent they belong to
const AGENT_COLORS = {
    'Niche Strategist': '#00d4ff',
    'Scout': '#00d4ff',
    'Strategizing': '#00d4ff',
    'Script Architect': '#a855f7',
    'Writer': '#a855f7',
    'Writing Script': '#a855f7',
    'Visual Director': '#f59e0b',
    'Designer': '#f59e0b',
    'Designing': '#f59e0b',
    'Media Synth': '#10b981',
    'Editor': '#10b981',
    'Synthesizing': '#10b981',
    'Assembler': '#f43f5e',
    'Assembling': '#f43f5e',
    'Critic': '#06b6d4',
    'Quality Audit': '#06b6d4',
    '✅': '#10b981',
    '✨': '#10b981',
    '❌': '#f43f5e',
    '🚀': '#00d4ff',
    '🤖': '#00d4ff',
}

function getLineColor(log) {
    for (const [key, color] of Object.entries(AGENT_COLORS)) {
        if (log.includes(key)) return color
    }
    return null
}

function formatLine(log, i) {
    const color = getLineColor(log)
    const isError = log.includes('❌') || log.toLowerCase().includes('error') || log.toLowerCase().includes('failed')
    const isSuccess = log.includes('✨') || log.includes('✅') || log.includes('COMPLETE')
    const isNode = log.includes('[Node]') || log.includes('[Router]')

    return (
        <div
            key={i}
            className={`log-line ${isError ? 'log-line--error' : ''} ${isSuccess ? 'log-line--success' : ''} ${isNode ? 'log-line--node' : ''}`}
            style={color && !isError && !isSuccess ? { color } : undefined}
        >
            <span className="log-ts">{String(i + 1).padStart(3, '0')}</span>
            <span className="log-text">{log}</span>
        </div>
    )
}

export default function LogTerminal({ logs, phase }) {
    const bottomRef = useRef(null)

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [logs])

    return (
        <div className="terminal">
            <div className="terminal-bar">
                <div className="terminal-dots">
                    <span className="dot dot--red" />
                    <span className="dot dot--yellow" />
                    <span className="dot dot--green" />
                </div>
                <span className="terminal-title">nexus-motion — agent logs</span>
                <div className={`terminal-status ${phase === 'running' ? 'terminal-status--live' : ''}`}>
                    {phase === 'running' ? '● LIVE' : phase === 'done' ? '✓ DONE' : ''}
                </div>
            </div>
            <div className="terminal-body">
                <div className="terminal-prompt">
                    <span className="prompt-sym">❯</span>
                    <span className="prompt-cmd">nexus-motion run</span>
                </div>
                {logs.map((log, i) => formatLine(log, i))}
                {phase === 'running' && (
                    <div className="log-cursor">
                        <span className="prompt-sym">❯</span>
                        <span className="cursor-blink">▋</span>
                    </div>
                )}
                <div ref={bottomRef} />
            </div>
        </div>
    )
}
