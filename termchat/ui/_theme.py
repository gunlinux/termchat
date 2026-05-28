"""Backend-neutral UI theme constants shared by the stdout and Textual UIs.

Per-platform colors are intentionally not shared: the stdout backend uses raw
ANSI escapes while the Textual backend uses Rich color names. Only the platform
icons are identical across backends, so they live here to avoid drift.
"""

# Nerd Font icon per platform ("" twitch, "" youtube).
PLATFORM_ICONS: dict[str, str] = {
    "twitch": "",  # Nerd Font nf-fa-twitch
    "youtube": "",  # Nerd Font nf-fa-youtube
    "fake": "◉",
    "system": "⚙",
}
