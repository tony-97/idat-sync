[app]
title = IDAT Sync
package.name = idatsync
package.domain = pe.edu.idat
source.dir = .
source.include_exts = py,png,jpg,html,jar,js,css
version = 0.0.2
requirements = python3,android,pyjnius,bottle,typing_extensions,pywebview,httpx,office365-rest-python-client,platformdirs,yt-dlp
orientation = portrait
fullscreen = 0

android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.add_jars = pywebview-android.jar
android.api = 34
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a

[buildozer]
log_level = 2
warn_on_root = 1
