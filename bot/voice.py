from __future__ import annotations

import asyncio
from pathlib import Path


def synthesize_arabic(text: str, out_path: Path) -> Path | None:
    """Create short Arabic voice note using edge-tts (free)."""
    try:
        import edge_tts
    except ImportError:
        return None

    # Keep voice notes short
    clean = " ".join(text.split())
    if len(clean) > 350:
        clean = clean[:347] + "..."

    voice = "ar-SA-HamedNeural"

    async def _run() -> None:
        communicate = edge_tts.Communicate(clean, voice)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await communicate.save(str(out_path))

    try:
        asyncio.run(_run())
    except RuntimeError:
        # nested loop fallback
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception:
        return None

    return out_path if out_path.exists() and out_path.stat().st_size > 0 else None


def voice_script_from_update(body: str, changed: bool) -> str:
    if not changed:
        return "تحديث. لا يوجد شيء جديد يا بطل."
    # Take first few meaningful lines
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()][:6]
    spoken = "تحديث. " + " ".join(lines)
    spoken = spoken.replace("≈", "حوالي").replace("|", ".").replace("$", " دولار ")
    return spoken
