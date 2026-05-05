# Crear Malla de Puntos — Complemento QGIS

Generador avanzado de mallas de puntos para QGIS con patrones hexagonales y rectangulares, alineación global de la malla (GR-01) para detección exacta de puntos en bordes, tres métodos de entrada de densidad (espaciado, pts/ha, ha/punto), cuatro modos de operación (Unificado, Contenedor, Islas, Individual), motor de aceleración Shapely ufunc (hasta 218×), simplificación Douglas-Peucker opcional, gestión de integridad en tres niveles, eliminación de huecos, capas de exclusión, mapeo de clases en JSON, densidad variable por clase, detección y limpieza de duplicados en bordes compartidos, informes de calidad ISO 19157:2023 y reproducibilidad mediante `_params.json`.

Advanced point grid generator for QGIS with hexagonal and rectangular patterns, global grid alignment (GR-01) for exact border-point detection, three density input methods (spacing, pts/ha, ha/point), four operation modes (Unified, Container, Islands, Individual), Shapely ufunc acceleration engine (up to 218×), optional Douglas-Peucker simplification, 3-way integrity management, hole elimination, exclusion layers, JSON class mapping, variable density per class, shared-border duplicate detection and cleanup, ISO 19157:2023 quality reports and reproducibility via `_params.json`.

---

## Instalación

### Opción 1 — Desde ZIP (recomendada)

1. Descargue el archivo `crear_malla_puntos.zip` desde la sección **Releases**.
2. En QGIS: **Complementos → Administrar e instalar complementos → Instalar desde ZIP**.
3. Seleccione el archivo descargado y haga clic en **Instalar**.
4. El algoritmo aparecerá en **Processing → Caja de herramientas → Herramientas Malla de Puntos**.

### Opción 2 — Manual (desarrollo)

```bash
cd ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
git clone https://github.com/jfallas56-CR/Crear-malla-de-Puntos.git crear_malla_puntos
```

Reinicie QGIS y active el complemento en **Complementos → Administrar e instalar complementos**.

---

## Requisitos

| Requisito | Versión mínima |
|---|---|
| QGIS | 3.28 |
| Python | 3.10 |
| Sistema operativo | Windows, Linux, macOS |

**Shapely** (librería de geometría computacional) se usa como motor de aceleración para operaciones punto-en-polígono. Está incluida por defecto en las instalaciones **standalone (.msi)** y **OSGeo4W** de QGIS 3.44 LTR y QGIS 4.0 — no requiere instalación adicional. Si no está disponible, el algoritmo usa `QgsGeometry.contains()` como respaldo automático sin pérdida de funcionalidad.

---

## Características

### Tipos de malla

- **Hexagonal** (recomendada): mayor isotropía espacial, distancia uniforme a los 6 vecinos más cercanos. Ideal para inventarios forestales y muestreos sistemáticos.
- **Rectangular**: cuadrícula ortogonal, fácil de exportar a tablas.

### Tres formas de definir la densidad

Solo uno de los tres parámetros debe tener un valor mayor a cero:

| Parámetro | Ejemplo |
|---|---|
| Espaciado (m) | 50 m entre puntos |
| Densidad (pts/ha) | 4 pts/ha → espaciado calculado automáticamente |
| Hectáreas/punto | 1 pto/10 ha → espaciado calculado automáticamente |

### Modos de operación

| Modo | Descripción | Archivo de salida |
|---|---|---|
| **Individual** | Cada polígono genera su grupo de puntos con ID de origen heredado en `ID_ORIGINAL` | `*_CON_ID_EXA/REC.gpkg` |
| **Conjunto — Unificado** | Malla sobre la unión geométrica de todos los polígonos | `*_UNIFICADO_EXA/REC.gpkg` |
| **Conjunto — Contenedor** | Malla sobre el polígono de mayor área — los demás no reciben puntos | `*_CONTENEDOR_EXA/REC.gpkg` |
| **Conjunto — Islas** | Malla solo en los polígonos distintos del contenedor | `*_ISLAS_EXA/REC.gpkg` |

### Guía de flujos de trabajo

| Caso de uso | Modo | Comportamiento | Tipo |
|---|---|---|---|
| Inventario forestal por parcela | Individual | — | Hexagonal |
| Muestreo en área única | Conjunto | Unificado | Hexagonal |
| Área con exclusiones internas (lago, edificio) | Conjunto | Contenedor | Hexagonal |
| Solo dentro de islas o fragmentos | Conjunto | Islas | Hexagonal |
| Densidad variable por clase de tamaño | Individual | — + JSON densidad | Hexagonal |

### Rendimiento — Motor de aceleración Shapely

El algoritmo usa el **API vectorizado (ufunc) de Shapely 2.x** para acelerar la verificación punto-en-polígono. La mejora es automática cuando Shapely está disponible:

| Configuración | Puntos | Sin Shapely | Con Shapely | Factor |
|---|---|---|---|---|
| 355 polígonos, 500 m | 236 252 | 340 s | 3,44 s | **99×** |
| 355 polígonos, 250 m | 944 928 | 1 329 s | 11,06 s | **120×** |
| 355 polígonos, 125 m | 3 779 584 | 9 417 s | 43,11 s | **218×** |

*Capa IGN CR Límites 1:25 000 — Intel Core Ultra 7 155H, QGIS 3.44 LTR*

### Simplificación de entrada (Douglas-Peucker)

Opcional — **desactivada por defecto**. Reduce el número de vértices antes de generar la malla. Con el motor Shapely activo la mejora de rendimiento es marginal (~10%). Útil principalmente para reducir artefactos geométricos en polígonos derivados de capas raster.

> **Precaución:** en polígonos con huecos pequeños (< 0,5 ha) o ancho reducido (< 10 m), una tolerancia de 5 m puede deformar el perímetro. Reducir la tolerancia a 0,5–1 m o desactivar la simplificación.

### Integridad geométrica

- **Reparar** (recomendado): aplica `makeValid()` de GEOS automáticamente.
- **Omitir**: descarta polígonos inválidos sin interrumpir el proceso.
- **No verificar (riesgo)**: procesa tal cual — puede producir resultados incorrectos.

### Reporte HTML con ISO 19157:2023

El reporte incluye:

- Puntos generados, área procesada, densidad solicitada vs. real
- **Calidad ISO 19157:2023**: Completitud (§D.1) y Consistencia Lógica (§D.3)
- Eficiencia de simplificación (% reducción de vértices)
- Sección de exclusión: área bruta, excluida y porcentaje
- Duplicados de borde: conteo, marcado y trazabilidad de eliminación
- Tabla completa de parámetros utilizados

---

## Campos de salida

### Modo Individual

| Campo | Tipo | Descripción |
|---|---|---|
| `id_punto` | Entero | Identificador secuencial único, comienza en 1 |
| `ID_ORIGINAL` | Mismo tipo que el campo fuente | Valor del campo ID del polígono de origen |
| `coord_x` | Double | Coordenada X en el SRC de la capa (2 decimales) |
| `coord_y` | Double | Coordenada Y en el SRC de la capa (2 decimales) |
| `en_borde` | Booleano | Verdadero si el punto está sobre un borde compartido entre polígonos adyacentes |

### Modo Conjunto

| Campo | Tipo | Descripción |
|---|---|---|
| `id_punto` | Entero | Identificador secuencial único, comienza en 1 |
| `coord_x` | Double | Coordenada X en el SRC de la capa (2 decimales) |
| `coord_y` | Double | Coordenada Y en el SRC de la capa (2 decimales) |
| `en_borde` | Booleano | Verdadero solo en modo Islas — siempre Falso en Unificado y Contenedor |

---

## Notas técnicas

- **SRC proyectado obligatorio**: la capa de entrada debe estar en un SRC en metros (UTM, CRTM05, etc.). Con SRC geográfico (grados, ej. EPSG:4326) la ejecución **se cancela con error crítico** — los cálculos de distancia y área serían completamente incorrectos.
- **Campo ID en modo Individual**: obligatorio, sin valores NULL. El algoritmo valida la ausencia de NULLs antes de iniciar y cancela si los detecta. Se recomienda usar un campo con valores únicos.
- **Archivos de salida**: si el archivo ya existe se añade `_V1`, `_V2`, etc. sin sobreescribir.
- **Identificación del contenedor**: en modo Contenedor, el polígono de mayor área se usa como contenedor. Si dos polígonos tienen igual área, se usa el primero según el orden en la capa.
- **Efecto de borde**: `contains()` estricto — puntos sobre el borde exacto del polígono pueden no incluirse. La densidad real puede ser levemente menor a la solicitada. Verificar en la tarjeta Densidad del reporte HTML.
- **`makeValid()`**: puede cambiar el tipo de geometría (Polygon → MultiPolygon). Las partes no poligonales residuales se descartan automáticamente.
- **Rejilla global (GR-01)**: el origen de la rejilla se calcula desde el rectángulo envolvente de la capa completa — garantiza que mallas de diferente espaciado sobre el mismo polígono sean espacialmente consistentes y que los puntos de borde sean detectables.

---

## Estructura del repositorio

```
crear_malla_puntos/
├── __init__.py                  # Punto de entrada del complemento
├── malla_puntos_plugin.py       # Clase principal, registro del provider
├── malla_puntos_provider.py     # Processing Provider
├── malla_puntos_algorithm.py    # Algoritmo principal (CrearMallaPuntos)
├── metadata.txt                 # Metadatos requeridos por QGIS
├── icons/
│   └── icon.svg                 # Ícono del complemento
├── README.md                    # Este archivo
├── LICENSE                      # Licencia GPL-2.0
└── CHANGELOG.md                 # Historial de versiones
```

---

## Generar el ZIP para distribución

```bash
cd ..
zip -r crear_malla_puntos.zip crear_malla_puntos/ \
    --exclude "*.pyc" \
    --exclude "*/__pycache__/*" \
    --exclude "*/.git/*" \
    --exclude "*.DS_Store"
```

---

## Licencia

Este complemento se distribuye bajo la licencia **GNU General Public License v2.0 o posterior**, compatible con QGIS.

Ver archivo [LICENSE](LICENSE) para el texto completo.

---

## Autor

**Jorge Fallas**
[jfallas56@gmail.com](mailto:jfallas56@gmail.com)

Reportar problemas en: [Issues](../../issues)
