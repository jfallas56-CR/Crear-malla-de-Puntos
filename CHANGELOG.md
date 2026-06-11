# Historial de cambios

Todos los cambios notables se documentan en este archivo.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).

---

## [1.0.1] — 2026-06-10

### Correcciones de publicación

- VERSION interna alineada a `v1.0.1` (antes: cadena de desarrollo `2026-05-04 v5z60`)
- Docstring del algoritmo: `QGIS >= 3.16` corregido a `QGIS >= 3.28`
- `icon.png` convertido a PNG RGBA 64×64 (antes: JPEG 128×128)
- README: badges de versión añadidos; ruta `icons/icon.svg` corregida a `icon.png`

---

## [1.0.0] — 2026-04-12

### Primera versión pública

#### Funcionalidades
- Generación de malla hexagonal (recomendada) y rectangular
- Tres formas de definir la densidad: Espaciado (m), Densidad (pts/ha), Hectáreas/punto
- Modo Individual: malla por polígono con herencia de campo ID
- Modo Conjunto: Contenedor, Islas y Unificado
- Simplificación de entrada Douglas-Peucker opcional (**desactivada por defecto**)
- Gestión de integridad geométrica: Reparar / Omitir / Procesar con riesgo
- Eliminación de huecos con control por área mínima y hueco estructural
- Reporte HTML con métricas completas
- Reporte de Calidad **ISO 19157:2023**: Completitud §D.1 y Consistencia Lógica §D.3
- Salida en GeoPackage (.gpkg) con nombres descriptivos por modo y tipo de malla
- Protección contra sobreescritura: sufijos `_V1`, `_V2`, etc.

#### Decisiones de diseño documentadas
- **Multihilo eliminado**: ganancia medida < 6% (5 s sobre 91 s) por limitación del GIL de Python.
  El cuello de botella real es la densidad de vértices, no el número de polígonos.
  La simplificación de entrada es la optimización efectiva cuando se activa (hasta 9.3×).
- **Hexagonal como tipo de malla por defecto**: mayor isotopía espacial para inventarios forestales.
- **Simplificación desactivada por defecto**: tolerancia 5 m produce diferencia de 1 punto
  sobre 235,898 (0.0004%) con ganancia de tiempo del 89% cuando se activa. Se desactiva
  por defecto para evitar deformación de polígonos con huecos pequeños (< 0,5 ha) o
  ancho reducido (< 10 m).

#### Rendimiento medido (235,898 puntos, simplificación activa)
| Configuración | Tiempo |
|---|---|
| Sin simplificación | ~841 s |
| Simplificación 5 m | ~91 s |
