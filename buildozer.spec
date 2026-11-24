[app]
title = IDAT Sync
package.name = idatsync
package.domain = pe.edu.idat
source.dir = .
source.include_exts = py,png,jpg,html,jar,js,css
version = 0.0.2
requirements = python3,kivy,android,pyjnius,openssl,certifi,bottle,typing_extensions,pywebview,httpx,office365-rest-python-client,platformdirs,yt-dlp,ffmpeg
orientation = portrait
fullscreen = 0

android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
# Copied from the installed pywebview package (webview/lib/pywebview-android.jar) before packaging:
android.add_jars = pywebview-android.jar
android.gradle_dependencies = androidx.core:core:1.13.1
android.add_resources = android/res/xml/file_paths.xml:xml/file_paths.xml
android.extra_manifest_application_arguments = ./android/fileprovider_manifest.xml
android.api = 34
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a

[buildozer]
log_level = 2
warn_on_root = 1
