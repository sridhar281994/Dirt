[app]

# (str) Title of your application
title = Buddymeet

# (str) Package name
package.name = frendschat

# (str) Package domain (needed for android/ios packaging)
package.domain = org.test

# (str) Source code where the main.py live
source.dir = .

# (str) Application entry point (relative to source.dir)
# python-for-android requires a root-level `main.py` inside the packaged app directory.
# Our repo root `main.py` routes to the Kivy app on Android and to the backend on server.
entrypoint = main.py

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas

# (list) List of inclusions using pattern matching
#source.include_patterns = assets/*,images/*.png

# (list) Source files to exclude (let empty to not exclude anything)
#source.exclude_exts = spec

# (list) List of directory to exclude (let empty to not exclude anything)
#source.exclude_dirs = tests, bin, venv

# (list) List of exclusions using pattern matching
#
# Reduce APK size:
# - only the Kivy frontend is needed on device
# - exclude backend/server code, local DB, caches, CI configs, etc.
#
# NOTE: This does not affect runtime; it only reduces packaged sources.
#
# Use list section below (more robust than a huge comma-separated line).
#source.exclude_patterns =

# (str) Application versioning (method 1)
version = 0.1

# (list) Application requirements
# comma separated e.g. requirements = sqlite3,kivy
#
# Keep requirements minimal to reduce APK/AAB size.
# NOTE: `pillow` is large and the frontend code doesn't import/use PIL, so omit it.
requirements = python3,kivy,requests,pyjnius

# Extra python-for-android flags.
#
# NOTE: Newer python-for-android versions no longer accept `--strip` / `--optimize`
# on the `apk` step (Buildozer passes `p4a.extra_args` to multiple toolchain
# commands), causing CI builds to fail with:
#   toolchain.py: error: unrecognized arguments: --strip --optimize=2
#
# If you want size optimizations back, we can re-add them using the current
# supported p4a/buildozer options for your pinned p4a version.
#p4a.extra_args = --strip --optimize=2

# (str) Custom source folders for requirements
# Sets custom source for any requirements with recipes
# requirements.source.kivy = ../../kivy

# (bool) Enable AndroidX support. Enable when 'android.api' >= 28.
android.enable_androidx = True


# (str) Presplash of the application
#presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icon of the application
icon.filename = %(source.dir)s/frontend_app/assets/icon.png

# (str) Supported orientation (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (list) List of service to declare
#services = NAME:ENTRYPOINT_TO_PY,NAME2:ENTRYPOINT2_TO_PY

#
# Android specific
#

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (string) Presplash background color (for android)
# Supported formats are: #RRGGBB #AARRGGBB or one of the following names:
# red, blue, green, black, white, gray, cyan, magenta, yellow, lightgray,
# darkgray, grey, lightgrey, darkgrey, aqua, fuchsia, lime, maroon, navy,
# olive, purple, silver, teal.
#android.presplash_color = #FFFFFF

# (list) Permissions
android.permissions = INTERNET,CAMERA,RECORD_AUDIO,MODIFY_AUDIO_SETTINGS,ACCESS_NETWORK_STATE

# Agora RTC SDK (Android)
# NOTE: Agora 4.5.0 publishes `full-sdk` on Maven Central; `rtc-sdk` is not
# available there, which caused CI Gradle resolution failures.
android.gradle_dependencies = io.agora.rtc:full-sdk:4.5.0

# Gradle repositories required to resolve Agora artifacts.
android.add_gradle_repositories = mavenCentral()

# Prefer AAB for smaller Play distribution (still can build APK for debugging).
android.release_artifact = aab

# (int) Target Android API, should be as high as possible.
android.api = 31

# (int) Minimum API your APK will support.
android.minapi = 21

# (int) Android SDK version to use
#android.sdk = 20

# (str) Android NDK version to use
#android.ndk = 19b

# (bool) Use --private data storage (True) or --dir public storage (False)
#android.private_storage = True

# (str) Android NDK directory (if empty, it will be automatically downloaded.)
#android.ndk_path =

# (str) Android SDK directory (if empty, it will be automatically downloaded.)
#android.sdk_path =

# (str) ANT directory (if empty, it will be automatically downloaded.)
#android.ant_path =

# (bool) If True, then skip trying to update the Android sdk
# This can be useful to avoid excess Internet downloads or save time
# when an update is due and you just want to test/build your package
# android.skip_update = False

# (bool) If True, then automatically accept SDK license
# agreements. This is intended for automation only. If set to False,
# the default, you will be shown the license when the build
# runs.
# android.accept_sdk_license = True

# (str) Android entry point, default is ok for Kivy-based app
#android.entrypoint = org.kivy.android.PythonActivity

# (list) Pattern to exclude from the final aab
#android.aab_exclude_patterns =

# (list) Pattern to exclude from the final apk
#android.apk_exclude_patterns =

# (int) Android app version code
#android.numeric_version = 1

# (str) Android app version name
#android.version_name = 1.0

# (str) Android additional libraries to copy into libs/armeabi
#android.add_libs_armeabi = libs/android/*.so
#android.add_libs_armeabi_v7a = libs/android-v7/*.so
#android.add_libs_arm64_v8a = libs/android-v8/*.so
#android.add_libs_x86 = libs/android-x86/*.so
#android.add_libs_mips = libs/android-mips/*.so

# (bool) Indicate whether the screen should stay on
# Don't forget to add the WAKE_LOCK permission if you set this to True
#android.wakelock = False

# (list) Android application meta-data to set (key=value format)
#android.meta_data =

# (list) Android library project to add (will be added in the
# project.properties automatically.)
#android.library_references =

# (str) Android logcat filters to use
#android.logcat_filters = *:S python:D

# (str) Android additional adb arguments
#android.adb_args = -H host.docker.internal

# (bool) Copy library instead of making a libpymodules.so
#android.copy_libs = 1

# (str) The Android arch to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
android.archs = arm64-v8a

# (int) overrides automatic versionCode computation (used in build.gradle)
# this is not the same as app version and should only be edited if you know what you're doing
# android.numeric_version = 1

# (bool) enables Android auto backup feature (Android API >= 23)
android.allow_backup = True

# (str) XML file for custom backup rules (see official auto backup documentation)
# android.backup_rules =

# (str) If you need to insert variables into your AndroidManifest.xml file,
# you can do so with the manifestPlaceholders property.
# This property takes a map of key-value pairs. (one per line)
# android.manifest_placeholders = {
#     'myCustomPlaceholder': 'myCustomValue'
# }

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1

# (str) Path to build artifact storage, absolute or relative to spec file
# build_dir = ./.buildozer

# (str) Path to build output storage, absolute or relative to spec file
# bin_dir = ./bin

#    -----------------------------------------------------------------------------
#    List as sections
#
#    You can define all the "list" as [section:name].
#    Each line will be considered as a option to the list.
#    Let's take [app] / source.exclude_patterns.
#    Instead of doing:
#
#        [app]
#        source.exclude_patterns = license,data/audio/*.wav,data/images/original/*
#
#    This can be translated into:
#
#        [app:source.exclude_patterns]
#        license
#        data/audio/*.wav
#        data/images/original/*
#

#    -----------------------------------------------------------------------------
#    Profiles
#
#    You can extend section / key with a profile
#    For example, you want to deploy a demo version of your application without
#    HD content. You could first change the title to add "(demo)" in the name
#    and extend the excluded directories to remove the HD content.
#
#        [app@demo]
#        title = My Application (demo)
#
#        [app:source.exclude_patterns@demo]
#        images/hd/*
#
#    Then, invoke buildozer with the "demo" profile:
#
#        buildozer --profile demo android debug


[app:source.exclude_patterns]
__pycache__/*
*/__pycache__/*
*/*/__pycache__/*
*/*/*/__pycache__/*
*.pyc
*.pyo
*.pyd
*.zip
.git/*
.github/*
.buildozer/*
bin/*
apache-ant-*
apache-ant-*/*
apache-ant-*/*/*
apache-ant-*/*/*/*
tmp_kivy221/*
tmp_kivyzip/*
tmp_kivysdist/*
tmp_*/*
app.db
*.db
*.sqlite
*.sqlite3
routers/*
scripts/*
core/*
utils/*
database.py
models.py
render-db-migrate.yml
requirements.txt
