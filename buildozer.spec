[app]
title = Afinia
package.name = afinia
package.domain = org.afinia
version = 1.0.0

# Código fuente
source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,atlas
source.exclude_dirs = .git,__pycache__,.github,bin,.buildozer,venv,.venv

# Requisitos de Python
requirements = python3,kivy==2.3.0,numpy,pyjnius

# Configuración de Android
android.permissions = RECORD_AUDIO,INTERNET
android.api = 34
android.minapi = 23
android.ndk = 27c
android.archs = arm64-v8a
android.accept_sdk_license = True

# Opciones de compilación
android.release_artifact = apk
android.logcat_filters = *:S python:D

# Interfaz
orientation = portrait
fullscreen = 0

[buildozer]
log_level = 2
warn_on_root = 1
