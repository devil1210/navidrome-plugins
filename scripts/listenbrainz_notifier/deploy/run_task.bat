@echo off
REM ==============================================================================
REM Script para ejecutar ListenBrainz Notifier con el Programador de Tareas de Windows
REM ==============================================================================

set SCRIPT_DIR=%~dp0..
cd /d "%SCRIPT_DIR%"

python notifier.py --check
