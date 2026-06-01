bot/
|-- main.py                  # Arranque, on_ready, on_message y registro de views
|-- config.py                # Variables de entorno, constantes, canales y roles
|-- state.py                 # Estado global en memoria del bot
|-- database.py              # Conexiones, migraciones y helpers de persistencia
|-- utils.py                 # Utilidades generales y traducciones
|-- operativo.py             # Ciclo de operativo, verificacion, auto-cierre y restauracion
|-- asistencia.py            # Asistencia por eventos, asistencia semanal y Sheets
|-- alertas.py               # Alertas de retiros, devoluciones y notificaciones
|-- antirrobo.py             # Sistema antirrobo y whitelist
|-- clips.py                 # Sistema de clips y paneles
|-- justificaciones.py       # Moderacion y flujo de justificacion de texto
|-- eventos_discord.py       # Deteccion de links de eventos de Discord por canal
|-- log_actions.py           # Log de acciones administrativas
|-- parser.py                # Parsing de embeds y texto libre del canal de armario
|-- licencia.py              # Verificacion de licencia del bot
|-- asistencia_plantilla.py  # Plantilla activa de asistencia
|-- sheets.py                # Integracion con Google Sheets
|-- views/
|   |-- __init__.py
|   |-- validar_view.py      # View de verificacion y razon de retiro
|   |-- retiros_view.py      # Dropdowns y detalle de retiros
|   |-- alertas_view.py      # Configuracion visual de alertas
|   |-- antirrobo_view.py    # Panel visual de antirrobo
|   |-- clips_view.py        # Paneles de clips
|   `-- voice_view.py        # Panel administrativo de voz
`-- commands/
    |-- __init__.py
    |-- cmd_stats.py         # /armas, /balas, /pistolas, /arma_blanca, /otros, /drogas
    |-- cmd_operativo.py     # /inicio_operativo, /terminar_operativo, /config_verificacion, /vincular_operativo
    |-- cmd_alertas.py       # /apagar_alertas, /encender_alertas, /configurar_alertas
    |-- cmd_admin.py         # /retiros_pendientes, /sync, /sincronizar_historial_texto
    |-- cmd_asistencia.py    # /asistencia, /asistencia_semanal_activar, /asistencia_semanal_desactivar, /asistencia_semanal_estado, /vincular_operativo
    `-- cmd_misc.py          # /help, /antirrobo, /whitelist_antirrobo

Notas de persistencia:
- `estado_operativo`
- `config_asistencia_semanal`
- `asistencia_semanal`
- `justificaciones_texto`
- tablas de alertas, antirrobo y registros de armario ya existentes
