import './VideoPlayer.css'

export default function VideoPlayer({ jobId }) {
    const videoUrl = `/api/video/${jobId}`

    return (
        <div className="video-wrap glass">
            <div className="video-header">
                <div className="video-indicator" />
                <span>Final Assembled Reel</span>
            </div>
            <div className="video-container">
                <video
                    className="video-el"
                    src={videoUrl}
                    controls
                    autoPlay
                    loop
                    playsInline
                >
                    Your browser does not support the video tag.
                </video>
            </div>
            <div className="video-actions">
                <a
                    className="video-download-btn"
                    href={videoUrl}
                    download="nexus-motion-reel.mp4"
                >
                    ⬇ Download Reel
                </a>
            </div>
        </div>
    )
}
