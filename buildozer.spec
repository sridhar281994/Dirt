[app]
title = Buddymeet
package.name = buddymeet
package.domain = com.srtech
version = 0.1.0

# Root-level entrypoint is REQUIRED for python-for-android in this repo.
# It dispatches to the Kivy app on Android and to the backend on server.
entrypoint = main.py

# Include the whole repo so `frontend_app/` and assets are packaged.
source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,webp,gif,atlas,json,ttf,otf,txt,yml,yaml
source.exclude_exts = pyc,pyo
source.exclude_patterns = .git/*,__pycache__/*,.venv/*,venv/*,node_modules/*,.gradle/*,*.db

# Keep Android requirements minimal (do NOT include backend deps like fastapi/uvicorn/sqlalchemy).
requirements = python3,kivy,requests,urllib3,certifi,idna,chardet

# Kivy / app settings
orientation = portrait
fullscreen = 1

# Optional: show a presplash if you add one later.
# presplash.filename = frontend_app/assets/presplash.png
icon.filename = frontend_app/assets/icon.png

[buildozer]
log_level = 2
warn_on_root = 1

[android]
# API levels
android.api = 34
android.minapi = 21

# Build outputs: debug APK by default (workflow calls `buildozer android debug`)
# For release/AAB you can run: `buildozer android release` or `buildozer android aab`

# Architectures
android.archs = arm64-v8a,armeabi-v7a

# Permissions needed for video + network + billing
# Use fully-qualified names so AndroidManifest always gets proper `uses-permission` entries
# (fixes "No permissions requested" in Android settings when shorthands aren't expanded).
android.permissions = android.permission.INTERNET,android.permission.ACCESS_NETWORK_STATE,android.permission.CAMERA,android.permission.RECORD_AUDIO,android.permission.WAKE_LOCK,android.permission.MODIFY_AUDIO_SETTINGS,android.permission.FOREGROUND_SERVICE,android.permission.FOREGROUND_SERVICE_CAMERA,android.permission.FOREGROUND_SERVICE_MICROPHONE,com.android.vending.BILLING

# Use Gradle (required for modern Android + dependencies)
android.enable_androidx = True
android.gradle_dependencies = com.android.billingclient:billing:6.1.0,io.agora.rtc:full-sdk:4.1.1

# If your CI has trouble downloading Ant, workflow forces android.ant_path=/usr
# android.ant_path = /usr

