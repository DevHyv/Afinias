[app]
# Información básica
title = Afinia
package.name = afinia
package.domain = org.afinia

# Código de la app
source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,atlas
source.exclude_dirs = .git,__pycache__,.github,bin,.buildozer

version = 1.0.0

# Dependencias Python
requirements = python3,kivy==2.3.0,numpy,pyjnius

# Android
android.permissions = RECORD_AUDIO,INTERNET
android.api = 35
android.minapi = 23
android.ndk = 27c
android.archs = arm64-v8a
android.accept_sdk_license = True

# Interfaz
orientation = portrait
fullscreen = 0

# Icono opcional: si no tienes icon.png, comenta esta línea
# icon.filename = icon.png

[buildozer]
log_level = 2
warn_on_root = 1

# Conserva el build en CI para acelerar compilaciones posteriores
# app.requirement.python3 = 3.11
