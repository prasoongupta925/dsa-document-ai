# Changelog

## [0.2.0] - 2026-06-16

### Changed
- Upgraded pipecat-ai from 0.0.102 to 1.3.0
- Replaced custom Smallest AI STT/TTS vendors with pipecat built-in services (`pipecat.services.smallest`)
- Removed custom Nova Sonic patch in favor of pipecat's built-in `pipecat.services.aws.nova_sonic`

### Fixed
- Nova Sonic tool calls not responding on first attempt due to race condition between TranscriptionUserTurnStartStrategy and tool result delivery
- Configured user turn strategy with `enable_interruptions=True, enable_user_speaking_frames=False` to allow both tool results and barge-in interruptions to work correctly

### Improved
- Optimized system prompt for better Hindi/Hinglish responses with proper script detection, natural number formatting, and feminine verb forms
- Added steering criteria for language matching, conciseness, and spoken-form numbers

### Removed
- `vendors/` folder (custom Smallest STT/TTS implementations)
- `patch/` folder (custom Nova Sonic LLM patch)

## [0.1.0] - 2026-03-12

### Added
- Real-time multilingual voice assistant with support for Hindi, Bengali, Tamil, Telugu, Kannada, Malayalam, Marathi, Gujarati, Odia, Punjabi, Assamese, and English
- Pipecat-based pipeline with pluggable STT → LLM → TTS architecture
- Amazon Nova Sonic speech-to-speech integration
- Smallest AI STT/TTS for broader Indic language coverage
- React frontend with 3D animated avatar and spectrogram visualizations
- FastAPI WebSocket backend for real-time bidirectional audio streaming
- Silero VAD for natural turn-taking and interruption handling
- Tool-calling support via Strands Agents and Pipecat function calling
- AWS CDK infrastructure for VPC, ECS, and Cognito-based authentication
