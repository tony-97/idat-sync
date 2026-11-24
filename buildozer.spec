[app]
title = IDAT Sync
package.name = idatsync
package.domain = pe.edu.idat
source.dir = src
source.include_exts = py,png,jpg,html,jar,js,css
version = 1.0.0
requirements = python3,kivy,android,pyjnius,openssl,certifi,bottle,typing_extensions,pywebview,httpx,office365-rest-python-client>=3.3.1,charset-normalizer==3.3.2,platformdirs,yt-dlp,ffmpeg
orientation = portrait
fullscreen = 0

android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
# Copied from the installed pywebview package (webview/lib/pywebview-android.jar) before packaging:
android.add_jars = pywebview-android.jar
android.gradle_dependencies = androidx.core:core:1.13.1
android.add_resources = android/res/xml/file_paths.xml:xml/file_paths.xml
p4a.hook = android/p4a_hook.py
android.api = 34
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 0
