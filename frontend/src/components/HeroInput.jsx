import { useState } from 'react'
import './HeroInput.css'

export default function HeroInput({ onSubmit, isRunning }) {
    const [value, setValue] = useState('')

    const handleSubmit = (e) => {
        e.preventDefault()
        if (!value.trim() || isRunning) return
        onSubmit(value.trim())
    }

    const examples = [
        'Fitness tips for busy people — 3 AM wake up routine',
        'Crypto secrets Wall Street doesn\'t want you to know',
        'How to make $500/day with AI tools nobody uses',
    ]

    return (
        <section className="hero">
            <div className="hero-eyebrow">
                <div className="eyebrow-pip" />
                AUTONOMOUS AI VIDEO AGENCY
            </div>

            <h1 className="hero-title">
                Turn a <span className="gradient-text">Niche + Hook</span>
                <br />into a Viral Reel
            </h1>

            <p className="hero-subtitle">
                A 6-agent LangGraph pipeline that strategizes, scripts, designs visuals,
                synthesizes media, assembles, and quality-audits your reel — fully autonomously.
            </p>

            <form className="hero-form" onSubmit={handleSubmit}>
                <div className="input-wrapper">
                    <div className="input-icon">🎯</div>
                    <input
                        id="niche-input"
                        type="text"
                        className="hero-input"
                        placeholder="Describe your niche + hook idea…"
                        value={value}
                        onChange={e => setValue(e.target.value)}
                        disabled={isRunning}
                        autoComplete="off"
                    />
                    {value && !isRunning && (
                        <button
                            type="button"
                            className="input-clear"
                            onClick={() => setValue('')}
                            aria-label="Clear"
                        >✕</button>
                    )}
                </div>

                <button
                    id="launch-btn"
                    type="submit"
                    className={`launch-btn ${isRunning ? 'launch-btn--running' : ''}`}
                    disabled={!value.trim() || isRunning}
                >
                    {isRunning ? (
                        <>
                            <span className="spinner" />
                            Agency Running…
                        </>
                    ) : (
                        <>
                            <span className="btn-icon">🚀</span>
                            Launch Agency
                        </>
                    )}
                </button>
            </form>

            <div className="example-prompts">
                <span className="example-label">Try:</span>
                {examples.map((ex, i) => (
                    <button
                        key={i}
                        className="example-chip"
                        onClick={() => setValue(ex)}
                        disabled={isRunning}
                    >
                        {ex}
                    </button>
                ))}
            </div>
        </section>
    )
}
