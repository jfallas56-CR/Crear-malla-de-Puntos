Crear Mallas de Puntos: Hexagonal o Rectangular
![QGIS](https://img.shields.io/badge/QGIS-3.28%20LTR%20%7C%203.44%20LTR%20%7C%204.0-brightgreen?logo=qgis)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![License](https://img.shields.io/badge/License-GPL%20v2-yellow)
![Version](https://img.shields.io/badge/version-1.0.1-orange)
Complemento de Processing para QGIS que genera mallas sistemáticas de puntos (hexagonal o rectangular) dentro de capas de polígonos. Motor de aceleración Shapely ufunc (hasta 218× más rápido), alineación global de rejilla (GR-01), cuatro modos de operación, densidad variable por clase JSON y reporte de calidad ISO 19157:2023.
---
Instalación
Desde el Plugin Repository (recomendado)
Complementos → Administrar e instalar complementos → Buscar: `Crear Mallas de Puntos`
Desde ZIP
Descargue `crear_mallas_puntos.zip` desde Releases.
Complementos → Administrar e instalar complementos → Instalar desde ZIP.
El algoritmo aparece en Processing → Caja de herramientas → Herramientas Malla de Puntos.
Desde el código fuente
```bash
cd ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
git clone https://github.com/jfallas56-CR/Crear-malla-de-Puntos.git crear_mallas_puntos
```
Reinicie QGIS y active el complemento en Complementos → Administrar e instalar complementos.
---
Requisitos
Requisito	Versión mínima
QGIS	3.28 LTR
Python	3.10
Sistema operativo	Windows, Linux, macOS
Shapely se usa como motor de aceleración para operaciones punto-en-polígono. Está incluida en las instalaciones standalone y OSGeo4W de QGIS 3.44 LTR y QGIS 4.0 — no requiere instalación adicional. Si no está disponible, el algoritmo usa `QgsGeometry.contains()` como respaldo automático sin pérdida de funcionalidad.
---
Características
Tipos de malla
Tipo	Descripción	Uso recomendado
Hexagonal	Distancia uniforme a los 6 vecinos — mayor isotropía espacial	Inventarios forestales, muestreos sistemáticos
Rectangular	Cuadrícula ortogonal	Exportación a tablas, compatibilidad con sistemas externos
Definición de densidad
Solo uno de los tres parámetros debe ser mayor a cero:
Parámetro	Ejemplo	Resultado
Espaciado (m)	`50`	50 m entre puntos
Densidad (pts/ha)	`4`	Espaciado calculado automáticamente
Hectáreas / punto	`10`	1 punto cada 10 ha
Modos de operación
Modo	Descripción	Archivo de salida
Individual	Cada polígono genera su grupo de puntos; el ID de origen se hereda en `ID_ORIGINAL`	`*_CON_ID_EXA/REC.gpkg`
Conjunto — Unificado	Malla sobre la unión geométrica de todos los polígonos	`*_UNIFICADO_EXA/REC.gpkg`
Conjunto — Contenedor	Malla sobre el polígono de mayor área	`*_CONTENEDOR_EXA/REC.gpkg`
Conjunto — Islas	Malla solo en los polígonos distintos del contenedor	`*_ISLAS_EXA/REC.gpkg`
Guía de flujos de trabajo
Caso de uso	Modo recomendado	Tipo de malla
Inventario forestal por parcela	Individual	Hexagonal
Muestreo en área única continua	Conjunto — Unificado	Hexagonal
Área con exclusiones internas (lago, edificio)	Conjunto — Contenedor	Hexagonal
Solo dentro de fragmentos o islas	Conjunto — Islas	Hexagonal
Densidad variable según clase de uso	Individual + JSON densidad	Hexagonal
---
Rendimiento — Motor Shapely ufunc
El algoritmo usa el API vectorizado (ufunc) de Shapely 2.x para acelerar la verificación punto-en-polígono. La aceleración es automática cuando Shapely está disponible:
Configuración	Puntos	Sin Shapely	Con Shapely	Factor
355 polígonos, espaciado 500 m	236 252	340 s	3,44 s	99×
355 polígonos, espaciado 250 m	944 928	1 329 s	11,06 s	120×
355 polígonos, espaciado 125 m	3 779 584	9 417 s	43,11 s	218×
Capa IGN CR Límites 1:25 000 — Intel Core Ultra 7 155H, QGIS 3.44 LTR
---
Funcionalidades adicionales
Simplificación de entrada (Douglas-Peucker)
Opcional, desactivada por defecto. Reduce el número de vértices antes de generar la malla. Con Shapely activo la mejora de rendimiento es marginal (~10%). Útil principalmente para reducir artefactos en polígonos derivados de capas raster.
> **Precaución:** en polígonos con huecos pequeños (< 0,5 ha) o ancho reducido (< 10 m), una tolerancia de 5 m puede deformar el perímetro. Use 0,5–1 m o desactive la simplificación.
Integridad geométrica
Opción	Comportamiento
Reparar (recomendado)	Aplica `makeValid()` de GEOS automáticamente
Omitir	Descarta polígonos inválidos sin interrumpir el proceso
No verificar (riesgo)	Procesa tal cual — puede producir resultados incorrectos
Eliminación de huecos
Control por área mínima y preservación de hueco estructural. Permite eliminar huecos internos pequeños (ej. vacíos de digitalización) conservando los huecos intencionales del diseño.
Capa de exclusión
Define zonas donde no se generarán puntos, con búfer opcional y transformación automática de SRC si difiere del de la capa de entrada.
Mapeo de clases JSON
En modo Individual, permite asignar densidades distintas a cada clase de polígono mediante un archivo JSON externo. El campo de clase es configurable.
Detección de duplicados en bordes compartidos
En modo Individual, los puntos que caen exactamente sobre el borde entre dos polígonos adyacentes se marcan con `en_borde = True`. El algoritmo los detecta y puede eliminarlos para evitar doble conteo.
Reporte HTML con ISO 19157:2023
El reporte de calidad incluye:
Puntos generados, área procesada, densidad solicitada vs. real
Completitud §D.1 y Consistencia Lógica §D.3 (ISO 19157:2023)
Eficiencia de simplificación (% reducción de vértices)
Sección de exclusión: área bruta, excluida y porcentaje
Duplicados de borde: conteo, marcado y trazabilidad de eliminación
Tabla completa de parámetros utilizados
Trazabilidad — `_params.json`
El algoritmo puede exportar un archivo `_params.json` con todos los parámetros de la ejecución, lo que permite reproducir exactamente los mismos resultados en cualquier momento.
---
Campos de salida
Modo Individual
Campo	Tipo	Descripción
`id_punto`	Entero	Identificador secuencial único, comienza en 1
`ID_ORIGINAL`	Mismo tipo que el campo fuente	Valor del campo ID del polígono de origen
`coord_x`	Double	Coordenada X en el SRC de la capa (2 decimales)
`coord_y`	Double	Coordenada Y en el SRC de la capa (2 decimales)
`en_borde`	Booleano	`True` si el punto está sobre un borde compartido entre polígonos adyacentes
Modo Conjunto
Campo	Tipo	Descripción
`id_punto`	Entero	Identificador secuencial único, comienza en 1
`coord_x`	Double	Coordenada X en el SRC de la capa (2 decimales)
`coord_y`	Double	Coordenada Y en el SRC de la capa (2 decimales)
`en_borde`	Booleano	`True` solo en modo Islas — siempre `False` en Unificado y Contenedor
---
Notas técnicas
SRC proyectado obligatorio. La capa de entrada debe estar en un SRC en metros (UTM, CRTM05, etc.). Con SRC geográfico (grados, ej. EPSG:4326) la ejecución se cancela con error crítico — los cálculos de distancia y área serían incorrectos.
Campo ID en modo Individual. Obligatorio, sin valores NULL. El algoritmo valida la ausencia de NULLs y duplicados antes de iniciar y cancela si los detecta.
Archivos de salida. Si el archivo ya existe se añade `_V1`, `_V2`, etc. sin sobreescribir.
Identificación del contenedor. En modo Contenedor, el polígono de mayor área actúa como contenedor. Si dos polígonos tienen igual área, se usa el primero según el orden en la capa.
Efecto de borde. `contains()` estricto — puntos sobre el borde exacto del polígono pueden no incluirse. La densidad real puede ser levemente menor a la solicitada. Verificar en la tarjeta Densidad del reporte HTML.
`makeValid()`. Puede cambiar el tipo de geometría (Polygon → MultiPolygon). Las partes no poligonales residuales se descartan automáticamente.
Rejilla global (GR-01). El origen de la rejilla se calcula desde el rectángulo envolvente de la capa completa. Garantiza que mallas de diferente espaciado sobre el mismo polígono sean espacialmente consistentes y que los puntos de borde entre polígonos sean detectables con exactitud.
---
Estructura del repositorio
```
crear_mallas_puntos/
├── __init__.py                 # Punto de entrada del complemento
├── malla_puntos_plugin.py      # Clase principal, registro del provider
├── malla_puntos_provider.py    # Processing Provider
├── malla_puntos_algorithm.py   # Algoritmo principal (CrearMallaPuntos)
├── metadata.txt                # Metadatos requeridos por QGIS
├── icon.png                    # Ícono del complemento (PNG 64×64)
├── README.md                   # Este archivo
├── LICENSE                     # Licencia GPL-2.0
└── CHANGELOG.md                # Historial de versiones
```
---
Licencia
Distribuido bajo la GNU General Public License v2.0 o posterior, compatible con QGIS.
Ver LICENSE para el texto completo.
---
Autor
Jorge Fallas — jfallas56@gmail.com
Reportar problemas en: Issues
