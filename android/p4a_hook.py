"""Inject FileProvider into the generated AndroidManifest.xml before Gradle assemble."""

from pathlib import Path

MANIFEST_PATH = Path("src/main/AndroidManifest.xml")
PROVIDER_XML = (
    Path(__file__).resolve().parent / "fileprovider_manifest.xml"
).read_text(encoding="utf-8").strip()


def before_apk_assemble(_toolchain) -> None:
    """Insert the FileProvider <provider> element inside <application>."""
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    if "androidx.core.content.FileProvider" in manifest:
        return
    if "</application>" not in manifest:
        raise RuntimeError(f"Missing </application> tag in {MANIFEST_PATH}")
    MANIFEST_PATH.write_text(
        manifest.replace(
            "</application>", f"    {PROVIDER_XML}\n    </application>", 1
        ),
        encoding="utf-8",
    )
