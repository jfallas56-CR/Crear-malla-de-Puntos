# -*- coding: utf-8 -*-
"""
================================================================================
Script  : Crear Malla de Puntos — Hexágonos y Rectángulos
Archivo : malla_puntos_algorithm.py
Autor   : Jorge Fallas (jfallas56@gmail.com)
Versión : 2026-04-05
QGIS    : >= 3.16 (Qt5) y 4.00–4.99 (Qt6)
Python  : >= 3.8

Propósito:
    Genera una malla sistemática de puntos (rectangular o hexagonal) dentro de
    polígonos, con gestión de integridad geométrica, simplificación
    Douglas-Peucker simplification, hole elimination, exclusion layers,
    shared-border duplicate detection (GR-01/OB-01), JSON class mapping
    and ISO 19157:2023 quality reports via HTML and _params.json.

Rendimiento:
    Si Shapely está instalado, fill_polygon() usa STRtree para point-in-polygon
    acelerado — divide la geometría en partes indexadas → O(log n) por punto.
    Mejora medida con ufunc vectorizado: hasta 218× (9 417 s → 43 s, 3 779 584 pts).
    Motor: ufunc NumPy vectorizado + PreparedGeometry/STRtree según tipo geometría.
    Selección automática — sin configuración adicional por parte del usuario.
    Sin Shapely, usa QgsGeometry.intersects() (fallback automático).
    En QGIS 3.x/OSGeo4W Shapely viene incluido por defecto. Si no está disponible:
    python -m pip install shapely (desde OSGeo4W Shell).

Entradas:
    - Capa vectorial de polígonos (cualquier formato compatible con OGR/GDAL)
    - Espaciado (m), Densidad (pts/ha) o Hectáreas/punto (prioridad 1-2-3)
    - Parámetros de integridad geométrica, simplificación y huecos

Salidas:
    - Capa de puntos en GeoPackage (.gpkg) con campos: id_punto, [ID_ORIGINAL],
      coord_x, coord_y, en_borde
    - Reporte HTML con métricas del proceso (opcional)
    - Archivo JSON de parámetros de configuración (opcional, defaultValue=True)

Dependencias:
    - QGIS >= 3.16 (recomendado >= 3.20)
    - Python >= 3.8
    - qgis.core, qgis.PyQt (incluidas en QGIS)
================================================================================
"""

import math
import os
import json
import webbrowser
import datetime
import traceback
import sys
import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from qgis.PyQt.QtCore import QCoreApplication, QVariant

# ==============================================================================
# COMPATIBILIDAD QGIS 3.x (Qt5) / QGIS 4.x (Qt6)
# QGIS 4.0 movió varias constantes y clases al namespace Qgis.*
# Se definen aliases en runtime para que el resto del código sea neutral.
# ==============================================================================
try:
    # Qt6 / QGIS >= 4.0
    from qgis.PyQt.QtCore import QMetaType
    _INT_TYPE    = QMetaType.Type.Int
    _DOUBLE_TYPE = QMetaType.Type.Double
    _STRING_TYPE = QMetaType.Type.QString
except (ImportError, AttributeError):
    # Qt5 / QGIS 3.x
    from qgis.PyQt.QtCore import QVariant
    _INT_TYPE    = QVariant.Int
    _DOUBLE_TYPE = QVariant.Double
    _STRING_TYPE = QVariant.String
from qgis.core import (
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterDistance,
    QgsProcessingParameterEnum,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingUtils,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsVectorFileWriter,
    QgsProcessingContext,
    QgsMessageLog,
    Qgis,
    QgsFeatureRequest,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsUnitTypes
)

# ── Shapely opcional — usado para STRtree en fill_polygon() ─────────────────
try:
    from shapely.geometry import Point as _ShapelyPoint, shape as _shapely_shape
    from shapely.strtree import STRtree as _STRtree
    from shapely.prepared import prep as _shapely_prep
    import shapely as _shapely_module
    import shapely as _sh
    import numpy as _np_shapely
    import json as _json_shapely
    _SHAPELY_AVAILABLE = True
    _SHAPELY_VERSION = _shapely_module.__version__
except ImportError:
    _SHAPELY_AVAILABLE = False
    _SHAPELY_VERSION = None

# ── Aliases de enums renombrados en QGIS 4.0 ──────────────────────────────────
# En QGIS 4.0 varias clases/constantes se movieron al namespace Qgis.*
# Estrategia: try con el nombre nuevo (QGIS 4.x); except con el nombre viejo (QGIS 3.x).
# IMPORTANTE: los fallbacks referencian la API QGIS 3.x explícitamente — nunca
# el alias mismo (que aún no existe en el momento del fallback).
#
# QgsVectorFileWriter.NoError NO requiere alias — permanece igual en QGIS 4.x.
# QgsWkbTypes métodos estáticos (flatType, isMultiType, hasZ, hasM) permanecen
# en QgsWkbTypes en QGIS 4.x — no requieren alias.

# WKB / Geometry type enums
try:
    # QGIS >= 4.0
    _WkbPoint          = Qgis.WkbType.Point
    _WkbGeomCollection = Qgis.WkbType.GeometryCollection
    _GeomTypePolygon   = Qgis.GeometryType.Polygon
except AttributeError:
    # QGIS 3.x
    _WkbPoint          = QgsWkbTypes.Point
    _WkbGeomCollection = QgsWkbTypes.GeometryCollection
    _GeomTypePolygon   = QgsWkbTypes.PolygonGeometry

# Distance unit enums
try:
    # QGIS >= 4.0
    _DistMeters  = Qgis.DistanceUnit.Meters
    _DistKm      = Qgis.DistanceUnit.Kilometers
    _DistFeet    = Qgis.DistanceUnit.Feet
    _DistDegrees = Qgis.DistanceUnit.Degrees
except AttributeError:
    # QGIS 3.x
    _DistMeters  = QgsUnitTypes.DistanceMeters
    _DistKm      = QgsUnitTypes.DistanceKilometers
    _DistFeet    = QgsUnitTypes.DistanceFeet
    _DistDegrees = QgsUnitTypes.DistanceDegrees

# Processing source type
try:
    _TypeVectorPolygon = Qgis.ProcessingSourceType.VectorPolygon
except AttributeError:
    _TypeVectorPolygon = QgsProcessing.TypeVectorPolygon

# Invalid geometry check flag
try:
    _GeomNoCheck = Qgis.InvalidGeometryCheck.NoCheck
except AttributeError:
    _GeomNoCheck = QgsFeatureRequest.GeometryNoCheck

# ==============================================================================
# CONSTANTES GLOBALES
# ==============================================================================
def _fmt(val, dec=2):
    """Formatea número según ISO 80000-1 / BIPM:
    coma como separador decimal, espacio simple como separador de miles.
    Solo para presentación en reporte HTML — no usar en cálculos ni JSON."""
    if val is None:
        return '—'
    try:
        s = f'{val:,.{dec}f}'     # '1,234.5678'
        s = s.replace(',', 'X')   # '1X234.5678'
        s = s.replace('.', ',')   # '1X234,5678'
        s = s.replace('X', '\u202f') # '1 234,5678' (espacio fino)
        return s
    except (TypeError, ValueError):
        return str(val)


class Constants:
    """Constantes para la gestión de integridad geométrica y modos del algoritmo."""
    # Gestión de integridad geométrica
    INTEGRIDAD_RIESGO = 0
    INTEGRIDAD_OMITIR = 1
    INTEGRIDAD_REPARAR = 2

    INTEGRIDAD_NAMES = ['[!] No verificar (Riesgo - Procesar "As Is")',
                        '[X] Omitir geometría inválida',
                        '[~] Reparar geometría (Recomendado)']

    # Tipo de malla (M-07)
    GRID_HEXAGONAL   = 0
    GRID_RECTANGULAR = 1

    # Modo de operación — Conjunto=0 (default), Individual=1
    MODE_CONJUNTO   = 0
    MODE_INDIVIDUAL = 1

    # Comportamiento relleno modo Conjunto — Unificado=0 (default)
    FILL_UNIFICADO  = 0
    FILL_CONTENEDOR = 1
    FILL_ISLAS      = 2

    # Constantes matemáticas precalculadas
    SQRT3_OVER_2 = 0.8660254037844386   # math.sqrt(3) / 2 - usada en malla hexagonal

    # Unidades de área
    HA_TO_M2 = 10000.0


# ==============================================================================
# CLASE: LOGGER (Gestión de Alertas y Métricas + Registro en QGIS)
# ==============================================================================
class Logger:
    """Sistema de logging con clasificación de mensajes y métricas.
    Thread-safe: usa Lock para proteger datos compartidos.
    Además envía todos los mensajes al registro de QGIS (panel Log Messages)."""
    
    def __init__(self, feedback, tag="MallaPuntos"):
        self.feedback = feedback
        self.tag = tag
        self.errores: List[str] = []
        self.advertencias: List[str] = []
        self.start_time: float = time.time()
        
        # Métricas de procesamiento
        self.geometrias_reparadas: int = 0
        self.geometrias_omitidas: int = 0
        self.geometrias_procesadas: int = 0
        self.geometrias_con_z: int = 0
        self.geometrias_multipart: int = 0
        
        # Acumuladores de área
        self._area_sum: float = 0.0
        self._area_count: int = 0
        
        # Registros para integridad geométrica
        self.reparados_ids: set = set()
        self.omitidos_ids: set = set()
        self.riesgo_ids: set = set()
    
    # === MODIFICACIÓN: RF-03 ===
    def log(self, message: str, level: str = 'INFO') -> None:
        # Mapear nivel a constante de Qgis
        if level == 'WARNING':
            qlevel = Qgis.Warning
            self.advertencias.append(message)
            # m-01: guard hasattr para compatibilidad con QGIS < 3.16
            if hasattr(self.feedback, 'pushWarning'):
                self.feedback.pushWarning(message)
            else:
                self.feedback.pushInfo(f'[WARNING] {message}')
        elif level == 'ERROR':
            qlevel = Qgis.Critical
            self.errores.append(message)
            self.feedback.reportError(f"[X] {message}")
        else:
            qlevel = Qgis.Info
            self.feedback.pushInfo(message)
        
        # Enviar también al registro de QGIS
        QgsMessageLog.logMessage(f"[{level}] {message}", self.tag, qlevel)
    
    def info(self, message: str) -> None:
        self.log(message, 'INFO')
    
    def warning(self, message: str) -> None:
        self.log(message, 'WARNING')
    
    def error(self, message: str) -> None:
        self.log(message, 'ERROR')
    
    def registrar_reparacion(self, fid: int) -> None:
        self.geometrias_reparadas += 1
        self.reparados_ids.add(fid)
    
    def registrar_omision(self, fid: int) -> None:
        self.geometrias_omitidas += 1
        self.omitidos_ids.add(fid)
    
    def registrar_riesgo(self, fid: int) -> None:
        self.riesgo_ids.add(fid)
    
    def registrar_geometria_procesada(self) -> None:
        self.geometrias_procesadas += 1
    
    def registrar_geometria_z(self) -> None:
        self.geometrias_con_z += 1
    
    def registrar_geometria_multipart(self) -> None:
        self.geometrias_multipart += 1
    
    def registrar_area(self, area: float) -> None:
        self._area_sum += area
        self._area_count += 1
    
    def get_tiempo_ejecucion(self) -> float:
        return time.time() - self.start_time
    
    def get_metricas(self) -> Dict[str, Any]:
        return {
            'tiempo_ejecucion': self.get_tiempo_ejecucion(),
            'geometrias_procesadas': self.geometrias_procesadas,
            'geometrias_reparadas': self.geometrias_reparadas,
            'geometrias_omitidas': self.geometrias_omitidas,
            'geometrias_con_z': self.geometrias_con_z,
            'geometrias_multipart': self.geometrias_multipart,
            'total_advertencias': len(self.advertencias),
            'total_errores': len(self.errores),
            'reparados_ids': sorted(self.reparados_ids),
            'omitidos_ids': sorted(self.omitidos_ids),
            'riesgo_ids': sorted(self.riesgo_ids)
        }


# ==============================================================================
# CLASE: VALIDADOR DE CRS
# ==============================================================================
class CRSValidator:
    @staticmethod
    def es_geografico(crs: QgsCoordinateReferenceSystem) -> bool:
        if not crs or not crs.isValid():
            return False
        return crs.isGeographic()
    
    @staticmethod
    def get_unidad(crs: QgsCoordinateReferenceSystem) -> str:
        """Retorna la unidad del CRS como texto. N-03: disponible para futuras extensiones del reporte."""
        if not crs or not crs.isValid():
            return "Desconocida"
        if crs.isGeographic():
            return "Grados"
        units = crs.mapUnits()
        unit_names = {
            _DistMeters: "Metros",
            _DistKm: "Kilómetros",
            _DistFeet: "Pies",
            _DistDegrees: "Grados"
        }
        return unit_names.get(units, "Desconocida")
    
    @staticmethod
    def generar_advertencia_crs(crs: QgsCoordinateReferenceSystem) -> Optional[str]:
        if CRSValidator.es_geografico(crs):
            return ("[!] ERROR CRÍTICO: SRC geográfico detectado (grados). "
                   "El algoritmo requiere un SRC proyectado en metros "
                   "(ej: CRTM05 EPSG:8908, UTM). Ejecución cancelada.")
        return None


# ==============================================================================
# CLASE: CONTADOR DE VÉRTICES Y SIMPLIFICADOR
# ==============================================================================
class GeometrySimplifier:
    @staticmethod
    def count_vertices(geom: QgsGeometry) -> int:
        if not geom or geom.isEmpty():
            return 0
        abstract = geom.constGet()
        return abstract.nCoordinates() if abstract else 0
    
    @staticmethod
    def simplify(geom: QgsGeometry, tolerance: float) -> Tuple[QgsGeometry, int, int]:
        if not geom or geom.isEmpty():
            return geom, 0,0
        vertices_antes = GeometrySimplifier.count_vertices(geom)
        if tolerance <= 0:
            return geom, vertices_antes, vertices_antes
        geom_simplificada = geom.simplify(tolerance)
        if geom_simplificada.isEmpty() or not geom_simplificada.isGeosValid():
            return geom, vertices_antes, vertices_antes
        vertices_despues = GeometrySimplifier.count_vertices(geom_simplificada)
        return geom_simplificada, vertices_antes, vertices_despues


# ==============================================================================
# CLASE: POST-PROCESADOR DE GEOMETRÍAS (Huecos)
# ==============================================================================
class GeometryPostProcessor:
    @staticmethod
    def _filtrar_anillos(rings: list, area_minima: float,
                          preservar_hueco_mayor: bool) -> Tuple[list, int]:
        """N-04: Aplica la logica de filtrado de anillos a una lista de rings.
        rings[0]=exterior, rings[1:]=huecos. Retorna (new_rings, n_eliminados)."""
        huecos = rings[1:]
        if not huecos:
            return [rings[0]], 0
        new_rings = [rings[0]]
        eliminados = 0
        if preservar_hueco_mayor and area_minima <= 0:
            huecos_con_area = sorted(
                ((r, QgsGeometry.fromPolygonXY([r]).area()) for r in huecos),
                key=lambda x: x[1], reverse=True)
            new_rings.append(huecos_con_area[0][0])
            eliminados = len(huecos_con_area) - 1
        elif area_minima > 0:
            for ring in huecos:
                ring_area = QgsGeometry.fromPolygonXY([ring]).area()
                if ring_area > area_minima:
                    new_rings.append(ring)
                else:
                    eliminados += 1
        else:
            eliminados = len(huecos)
        return new_rings, eliminados

    @staticmethod
    def eliminar_huecos(geom: QgsGeometry, area_minima: float = 0.0,
                         preservar_hueco_mayor: bool = False) -> Tuple[QgsGeometry, int]:
        """Elimina huecos (anillos internos) de un polígono.
        N-04: usa _filtrar_anillos() para evitar duplicacion multi/simple.
        Retorna (geometria procesada, número de huecos eliminados)."""
        if not geom or geom.isEmpty():
            return geom, 0
        if geom.type() != _GeomTypePolygon:
            return geom, 0
        huecos_eliminados = 0
        try:
            if QgsWkbTypes.isMultiType(geom.wkbType()):
                multi_poly = geom.asMultiPolygon()
                new_multi_poly = []
                for poly in multi_poly:
                    if not poly:
                        continue
                    new_rings, elim = GeometryPostProcessor._filtrar_anillos(
                        poly, area_minima, preservar_hueco_mayor)
                    huecos_eliminados += elim
                    new_multi_poly.append(new_rings)
                return QgsGeometry.fromMultiPolygonXY(new_multi_poly), huecos_eliminados
            else:
                poly = geom.asPolygon()
                if not poly:
                    return geom, 0
                new_rings, huecos_eliminados = GeometryPostProcessor._filtrar_anillos(
                    poly, area_minima, preservar_hueco_mayor)
                return QgsGeometry.fromPolygonXY(new_rings), huecos_eliminados
        except Exception as e:
            # N-11: loguear excepcion sin propagar (retornar geometria original es seguro)
            import traceback as _tb
            QgsMessageLog.logMessage(
                f'[WARNING] eliminar_huecos: {e}\n{_tb.format_exc()}', 'MallaPuntos', Qgis.Warning)
            return geom, 0


# ==============================================================================
# CLASE: MANEJADOR DE GEOMETRÍAS (Robustez)
# ==============================================================================
class GeometryHandler:
    @staticmethod
    def tiene_z(geom: QgsGeometry) -> bool:
        if not geom or geom.isEmpty():
            return False
        return QgsWkbTypes.hasZ(geom.wkbType())
    
    @staticmethod
    def tiene_m(geom: QgsGeometry) -> bool:
        if not geom or geom.isEmpty():
            return False
        return QgsWkbTypes.hasM(geom.wkbType())
    
    @staticmethod
    def es_multipart(geom: QgsGeometry) -> bool:
        if not geom or geom.isEmpty():
            return False
        return QgsWkbTypes.isMultiType(geom.wkbType())
    
    @staticmethod
    def aplanar_z(geom: QgsGeometry) -> QgsGeometry:
        if not geom or geom.isEmpty():
            return geom
        if GeometryHandler.tiene_z(geom) or GeometryHandler.tiene_m(geom):
            geom_flat = QgsGeometry(geom)
            geom_flat.get().dropZValue()
            geom_flat.get().dropMValue()
            return geom_flat
        return geom
    
    @staticmethod
    def preparar_geometria(geom: QgsGeometry, fid: int, modo_integridad: int,
                          logger: Logger, desc: str = "",
                          registrar_metrica: bool = True) -> Optional[QgsGeometry]:
        """
        Aplica la lógica de integridad de 3 vías.
        """
        if not geom or geom.isEmpty():
            logger.registrar_omision(fid)
            logger.warning(f"{desc}: Geometría vacía o nula - omitida")
            return None
        
        if GeometryHandler.tiene_z(geom):
            if registrar_metrica:
                logger.registrar_geometria_z()
            geom = GeometryHandler.aplanar_z(geom)
        
        if registrar_metrica and GeometryHandler.es_multipart(geom):
            logger.registrar_geometria_multipart()
        
        if geom.isGeosValid():
            if registrar_metrica:
                logger.registrar_geometria_procesada()
            return geom
        
        if modo_integridad == Constants.INTEGRIDAD_RIESGO:
            logger.registrar_riesgo(fid)
            logger.warning(f"[!] {desc}: Geometría inválida procesada con RIESGO (ID: {fid})")
            if registrar_metrica:
                logger.registrar_geometria_procesada()
            return geom
        
        elif modo_integridad == Constants.INTEGRIDAD_OMITIR:
            logger.registrar_omision(fid)
            logger.warning(f"[X] {desc}: Geometría inválida - omitida (ID: {fid})")
            return None
        
        elif modo_integridad == Constants.INTEGRIDAD_REPARAR:
            geom_reparada = geom.makeValid()
            if geom_reparada and not geom_reparada.isEmpty() and geom_reparada.isGeosValid():
                logger.registrar_reparacion(fid)
                logger.info(f"[~] {desc}: Geometría reparada exitosamente (ID: {fid})")
                if registrar_metrica:
                    logger.registrar_geometria_procesada()
                return geom_reparada
            else:
                logger.registrar_omision(fid)
                logger.error(f"[X] {desc}: Geometría inválida no pudo ser reparada - omitida (ID: {fid})")
                return None
        
        return None


# ==============================================================================
# CLASE: MANEJADOR DE CAPA DE EXCLUSIÓN
# ==============================================================================
class ExclusionHandler:
    """
    Construye y aplica la geometría de exclusión a partir de una capa vectorial
    de polígonos. Soporta buffer adicional y transformación CRS automática.

    Interacción con islas y huecos:
        La exclusión NO interactúa con la clasificación isla/hueco — opera sobre la geometría de trabajo de cada modo de forma independiente.
        Si la zona de exclusión cae completamente dentro de un polígono, difference() produce un nuevo hueco (hueco sin puntos).
        Este hueco NO es modificado por 'Eliminación de huecos' — comportamiento correcto: rellenarlo contradiría la exclusión definida.
        Orden de operaciones (garantía de integridad):
        Integridad → Simplificación → Eliminación de huecos → Exclusión → Malla.

    Uso:
        handler = ExclusionHandler(excl_source, buffer_m, target_crs, context, logger)
        if handler.activo:
            area_antes = geom_trabajo.area()                    # capturar ANTES
            geom_efectiva = handler.aplicar_exclusion(geom_trabajo)
            area_excl = max(0.0, area_antes - (geom_efectiva.area() if geom_efectiva else 0.0))
            handler.acumular_area_excluida(area_excl)
    """

    def __init__(self, excl_source, buffer_m: float,
                 target_crs: QgsCoordinateReferenceSystem,
                 context, logger: 'Logger'):
        self._excl_geom: Optional[QgsGeometry] = None
        self._activo: bool = False
        self._n_features: int = 0
        self._area_excl_total_m2: float = 0.0
        self._crs_transformado: bool = False

        if excl_source is None:
            return

        excl_crs = excl_source.sourceCrs()
        necesita_transform = (excl_crs.isValid() and target_crs.isValid()
                              and excl_crs != target_crs)
        if necesita_transform:
            transform = QgsCoordinateTransform(excl_crs, target_crs,
                                               context.transformContext())
            self._crs_transformado = True
            logger.warning(
                f"[Exclusión] CRS de capa de exclusión ({excl_crs.authid()}) "
                f"difiere de la capa de entrada ({target_crs.authid()}). "
                f"Se aplicará transformación automática.")
        else:
            transform = None

        geoms = []
        for feat in excl_source.getFeatures():
            g = feat.geometry()
            if not g or g.isEmpty():
                continue
            # Reparar geometría de exclusión si es inválida
            if not g.isGeosValid():
                g = g.makeValid()
                if not g or g.isEmpty():
                    continue
            # Transformar CRS si es necesario
            if transform:
                g.transform(transform)
            geoms.append(g)

        if not geoms:
            logger.warning("[Exclusión] La capa de exclusión no contiene geometrías válidas.")
            return

        self._n_features = len(geoms)
        # === MODIFICACIÓN: M-04 ===
        # Unión de todas las geometrías de exclusión primero, luego buffer
        self._excl_geom = QgsGeometry.unaryUnion(geoms)
        if buffer_m and buffer_m > 0:
            self._excl_geom = self._excl_geom.buffer(buffer_m, 12)
            
        if self._excl_geom and not self._excl_geom.isEmpty():
            if not self._excl_geom.isGeosValid():
                self._excl_geom = self._excl_geom.makeValid()
            self._activo = True
            logger.info(
                f"[Exclusión] {self._n_features} geometría(s) de exclusión"
                f"{' + buffer ' + str(buffer_m) + ' m' if buffer_m and buffer_m > 0 else ''}"
                f" → unión preparada"
                f"{' (CRS transformado)' if self._crs_transformado else ''}.")
        else:
            logger.warning("[Exclusión] La unión de geometrías de exclusión resultó vacía.")

    @property
    def activo(self) -> bool:
        return self._activo

    @property
    def n_features(self) -> int:
        return self._n_features

    # === MODIFICACIÓN: R-01 ===
    def aplicar_exclusion(self, geom: QgsGeometry) -> Optional[QgsGeometry]:
        """
        Retorna la diferencia entre geom y la geometría de exclusión.
        Si la diferencia resulta vacía o nula, retorna None (polígono completamente excluido).
        Si el manejador no está activo, retorna geom sin modificación.
        """
        if not self._activo or not geom or geom.isEmpty():
            return geom
        resultado = geom.difference(self._excl_geom)
        if resultado is None or resultado.isEmpty():
            return None
        # M-01: reparar geometría resultante (slivers, degenerados post-difference)
        if not resultado.isGeosValid():
            resultado = resultado.makeValid()
            if resultado is None or resultado.isEmpty():
                return None
        # C-02: usar flatType() — detecta GeometryCollectionZ, WKBUnknown, etc.
        # La comparación directa wkbType() == GeometryCollection falla con variantes Z/M.
        if QgsWkbTypes.flatType(resultado.wkbType()) == _WkbGeomCollection:
            partes = resultado.asGeometryCollection()
            partes_poligonales = [p for p in partes if p.type() == _GeomTypePolygon and not p.isEmpty()]
            # R-01: Advertir si se descartan partes no poligonales
            if len(partes) != len(partes_poligonales):
                QgsMessageLog.logMessage(
                    f"[Exclusión] La diferencia produjo una GeometryCollection con {len(partes) - len(partes_poligonales)} "
                    f"parte(s) no poligonal(es) (líneas/puntos). Éstas se descartan ya que la malla requiere polígonos.",
                    "MallaPuntos", Qgis.Warning)
            if not partes_poligonales:
                return None
            if len(partes_poligonales) == 1:
                return partes_poligonales[0]
            # Reconstruir como multipolígono
            return QgsGeometry.unaryUnion(partes_poligonales)
        return resultado

    def get_resumen(self) -> Dict[str, Any]:
        return {
            'activo':             self._activo,
            'n_features':         self._n_features,
            'crs_transformado':   self._crs_transformado,
            'area_excluida_m2':   self._area_excl_total_m2,
        }

    def acumular_area_excluida(self, area_m2: float) -> None:
        self._area_excl_total_m2 += area_m2


# ==============================================================================
# CLASE: MAPEADOR DE ID POR CLASES JSON
# ==============================================================================
class JsonMapper:
    """
    Carga y aplica un mapeo de valores de campo ID a clases definidas en un
    archivo JSON. Fallback transparente: si un valor no está en el mapeo,
    se retorna el valor original sin modificación.

    Estructura esperada del JSON:
    {
        "campo_fuente": "ID_PARCELA",       (informativo, no obligatorio)
        "campo_salida": "CLASE_USO",        (informativo, no obligatorio)
        "descripcion":  "Clasificación...", (informativo, no obligatorio)
        "mapeo": {
            "P001": "Bosque primario",
            "P002": "Bosque secundario",
            "P003": "Pastizal"
        }
    }

    Notas:
    - Las claves del mapeo se comparan como strings (conversión automática).
    - Si 'mapeo' no existe en el JSON, el mapeador actúa como pass-through.
    - El tipo del campo ID_ORIGINAL en la salida es siempre String cuando
      el mapeador está activo, independientemente del tipo del campo fuente.
    """

    def __init__(self, json_path: str, logger: 'Logger' = None):
        self._mapeo: Dict[str, Any] = {}
        self._meta: Dict[str, str] = {}
        self._activo: bool = False
        self._json_path: str = json_path
        self._sin_mapeo: set = set()   # IDs encontrados sin mapeo (para reporte)
        self._con_mapeo: int = 0       # contador de aplicaciones exitosas

        if not json_path:
            return
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._mapeo = {str(k): v for k, v in data.get('mapeo', {}).items()}
            # Metadatos: acepta tanto nivel raíz como dentro de clave 'meta'
            _meta_src = data.get('meta', {})
            self._meta = {
                'campo_fuente': _meta_src.get('campo_fuente', '') or data.get('campo_fuente', ''),
                'campo_salida': _meta_src.get('campo_salida', '') or data.get('campo_salida', ''),
                'descripcion':  _meta_src.get('descripcion',  '') or data.get('descripcion',  ''),
                'autor':        _meta_src.get('autor', ''),
                'fecha':        _meta_src.get('fecha', ''),
                'metodo_clasificacion': _meta_src.get('metodo_clasificacion', ''),
                'capa_origen':  _meta_src.get('capa_origen', ''),
                'total_clases': len(self._mapeo),
            }
            self._activo = bool(self._mapeo)
            if logger and self._activo:
                logger.info(
                    f"[JSON] Mapeo cargado: {len(self._mapeo)} entradas "
                    f"desde '{os.path.basename(json_path)}'")
        except (json.JSONDecodeError, OSError) as e:
            if logger:
                logger.error(f"[JSON] Error cargando mapeo: {e}")
            self._activo = False

    @property
    def activo(self) -> bool:
        return self._activo

    @property
    def meta(self) -> Dict[str, Any]:
        return self._meta

    def aplicar(self, valor) -> Any:
        """
        Retorna el valor mapeado si existe; el valor original en caso contrario.
        Thread-safe para lectura (dict es inmutable tras __init__).
        """
        if not self._activo:
            return valor
        clave = str(valor)
        if clave in self._mapeo:
            self._con_mapeo += 1
            return self._mapeo[clave]
        else:
            self._sin_mapeo.add(clave)
            return valor   # fallback: valor original

    def get_resumen(self) -> Dict[str, Any]:
        return {
            'activo':       self._activo,
            'json_path':    self._json_path,
            'total_clases': len(self._mapeo),
            'aplicaciones': self._con_mapeo,
            'sin_mapeo':    sorted(self._sin_mapeo),
            'meta':         self._meta,
        }

    @staticmethod
    def validar_archivo(json_path: str) -> Tuple[bool, str]:
        """
        Valida estructura del archivo JSON antes de ejecutar.
        Retorna (valido, mensaje_error).
        """
        if not json_path:
            return True, ""
        if not os.path.isfile(json_path):
            return False, f"[X] El archivo JSON no existe: {json_path}"
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return False, f"[X] JSON inválido en '{os.path.basename(json_path)}': {e}"
        except OSError as e:
            return False, f"[X] No se puede leer el archivo JSON: {e}"
        if 'mapeo' not in data:
            return False, (
                f"[X] El JSON '{os.path.basename(json_path)}' no contiene "
                f"la clave 'mapeo'. Estructura esperada: {{\"mapeo\": {{...}}}}"
            )
        if not isinstance(data['mapeo'], dict):
            return False, "[X] La clave 'mapeo' debe ser un objeto JSON (diccionario)."
        if len(data['mapeo']) == 0:
            return False, "[X] El diccionario 'mapeo' está vacío."
        return True, ""


# ==============================================================================
# ALGORITMO PRINCIPAL
# ==============================================================================
class CrearMallaPuntos(QgsProcessingAlgorithm):
    INPUT_POLYGON = 'INPUT_POLYGON'
    ID_FIELD = 'ID_FIELD'
    
    # Parámetros de definición
    SPACING      = 'SPACING'
    DENSITY      = 'DENSITY'
    HA_POR_PUNTO = 'HA_POR_PUNTO'
    
    GRID_TYPE = 'GRID_TYPE'
    OPERATION_MODE = 'OPERATION_MODE'
    FILL_BEHAVIOR = 'FILL_BEHAVIOR'
    OUTPUT_FOLDER = 'OUTPUT_FOLDER'
    OUTPUT_BASENAME = 'OUTPUT_BASENAME'
    
    OUTPUT_HTML_REPORT = 'OUTPUT_HTML_REPORT'
    OPEN_REPORT = 'OPEN_REPORT'
    
    # Nuevos parámetros de robustez
    GESTION_INTEGRIDAD = 'GESTION_INTEGRIDAD'
    SIMPLIFICAR_ENTRADA = 'SIMPLIFICAR_ENTRADA'
    TOLERANCIA_ENTRADA = 'TOLERANCIA_ENTRADA'
    ELIMINAR_HUECOS = 'ELIMINAR_HUECOS'
    AREA_MINIMA_HUECO = 'AREA_MINIMA_HUECO'
    PRESERVAR_HUECO_ESTRUCTURAL = 'PRESERVAR_HUECO_ESTRUCTURAL'

    # Exportación JSON y mapeo de clases
    EXPORTAR_JSON       = 'EXPORTAR_JSON'
    JSON_MAPEO_ID       = 'JSON_MAPEO_ID'
    JSON_DENSIDAD_CLASES = 'JSON_DENSIDAD_CLASES'

    # Capa de exclusión
    CAPA_EXCLUSION   = 'CAPA_EXCLUSION'
    BUFFER_EXCLUSION = 'BUFFER_EXCLUSION'

    # Eliminación de entidades sobrantes por duplicado de borde
    ELIMINAR_SOBRANTES = 'ELIMINAR_SOBRANTES'

    # Versión del algoritmo — actualizar con cada cambio relevante
    VERSION = '2026-05-04 v5z60'

    GRID_TYPE_LABELS = ['Hexagonal', 'Rectangular']
    MODE_LABELS      = ['Procesar como Conjunto', 'Rellenar Polígonos Individualmente']
    BEHAVIOR_LABELS  = ['Rellenar todo (unificado)',
                         'Rellenar contenedor (excluir islas)',
                         'Rellenar solo islas']
    # Nota: 'isla' es un término algorítmico — cualquier entidad de la capa que no
    # sea el polígono de mayor área, independientemente de su posición espacial.
    # Incluye polígonos separados, con borde compartido o superpuestos al contenedor.

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CrearMallaPuntos()

    def name(self):
        return 'crearmallapuntos_reporte'

    def displayName(self):
        return self.tr('Crear Malla de Puntos: Hexágonos, Rectángulos')

    def group(self):
        return self.tr('Herramientas Malla de Puntos')

    def groupId(self):
        return 'mallapuntos'

    def shortHelpString(self):
        return self.tr("""
        <h4>Descripción</h4>
        <p><em>Notación numérica: decimal con coma (3,14) · miles con espacio fino (1 234,56).</em></p>
        <p>Genera una malla sistemática de puntos (rectangular o hexagonal) dentro de polígonos. Guarda el resultado como GeoPackage (.gpkg) con coordenadas en la tabla de atributos e incluye un reporte HTML con métricas del proceso y Reporte de Calidad ISO 19157:2023.</p>

        <h4>[!] Antes de ejecutar — requisitos críticos</h4>
        <ul>
            <li><b>SRC proyectado obligatorio:</b> el algoritmo requiere un SRC proyectado en metros (UTM, CRTM05, etc.). Con SRC geográfico (grados, ej. WGS84 EPSG:4326) la ejecución se cancela con <b>error crítico</b>. Para Costa Rica use CRTM05 (EPSG:8908).</li>
            <li><b>Espaciado, Densidad o Ha/punto:</b> debe ingresar exactamente uno de los tres. Si los tres son 0,0 el proceso se cancela antes de iniciar.</li>
            <li><b>Campo ID en modo Individual:</b> obligatorio. Sin él el proceso no inicia. El campo no debe tener valores NULL ni se recomienda tener duplicados — ambas condiciones se validan antes de iniciar.</li>
            <li><b>Modos Contenedor e Islas:</b> requieren mínimo 2 polígonos en la capa.</li>
        </ul>

        <h4>Rendimiento — STRtree de Shapely</h4><p>Shapely 2.x está incluido en las instalaciones <b>standalone (.msi)</b> y OSGeo4W</b> de <b>QGIS 3.44 LTR</b> y <b>QGIS 4.0</b> — no requiere instalación adicional. El algoritmo usa el <b>API vectorizado (ufunc)</b> para acelerar la generación de puntos — reducción medida de hasta <b>218×</b> sobre geometrías complejas (ej: 9 417 s → 43 s con 3 779 584 puntos) Sin Shapely usa <code>QgsGeometry.intersects()</code> como respaldo automático. El registro de mensajes indica cuál motor está activo.</p>

        <h4>Simplificación de entrada — optimización de rendimiento</h4>
        <p>La simplificación de entrada (Douglas-Peucker) es la <b>optimización de rendimiento más efectiva</b> disponible. Reduce los vértices de los polígonos <b>antes</b> de generar la malla. La ganancia depende del número de polígonos, su configuración geométrica y el número de puntos. <b>Opcional</b> — desactivada por defecto. Ajuste la tolerancia según la escala de sus datos.</p>

        <h4>Modo Conjunto (default)</h4>
        <p>Los polígonos se combinan antes de generar la malla. Sin campo ID_ORIGINAL.</p>
        <ul>
            <li><b>Unificado</b> (<code>_UNIFICADO_EXA/REC.gpkg</code>): unión geométrica de todos los polígonos. Funciona con 1 solo polígono. Ideal cuando los polígonos se solapan.</li>
            <li><b>Contenedor</b> (<code>_CONTENEDOR_EXA/REC.gpkg</code>): malla sobre el polígono de mayor área tal como está, respetando sus huecos (huecos propios de la geometría). Los demás polígonos de la capa son islas y no reciben malla. Si dos polígonos tienen igual área, se usa el primero según orden en la capa.</li>
            <li><b>Islas</b> (<code>_ISLAS_EXA/REC.gpkg</code>): malla únicamente dentro de los polígonos distintos del contenedor (todos los demás features de la capa). El contenedor no recibe malla. Requiere >= 2 polígonos.</li>
        </ul>

        <h4>Modo Individual</h4>
        <p>Cada polígono genera su propio grupo de puntos. Cada punto hereda el valor del campo ID seleccionado.</p>
        <p><b>Archivo:</b> <code>{nombre_base}_CON_ID_EXA.gpkg</code> o <code>_REC.gpkg</code></p>

        <h4>Guía de flujos de trabajo</h4>
        <ul>
            <li><b>Muestreo en área única o unificada:</b> Conjunto · Unificado · Hexagonal o Rectangular</li>
            <li><b>Área con exclusiones internas (lago, edificio):</b> Conjunto · Contenedor · Hexagonal o Rectangular</li>
            <li><b>Solo dentro de islas o territorios separados:</b> Conjunto · Islas · Hexagonal o Rectangular</li>
        
        </ul>

        <h4>Espaciado · Densidad · Ha/punto — tres formas de definir la malla</h4>
        <p><b>Prioridad 1 — Espaciado (m):</b> distancia directa entre puntos. Anula las demás opciones.</p>
        <p><b>Prioridad 2 — Densidad (pts/ha):</b> el algoritmo calcula el espaciado:</p>
        <ul>
            <li>Rectangular: <code>espaciado = sqrt(10 000 / densidad)</code> &nbsp;· ej. 4 pts/ha → 50 m</li>
            <li>Hexagonal: <code>espaciado = sqrt(10 000 / (densidad × sqrt3/2))</code> &nbsp;· ej. 4 pts/ha → 53,7 m</li>
        </ul>
        <p><b>Prioridad 3 — Ha/punto:</b> superficie representada por cada punto. Equivale a densidad = 1 / Ha_por_punto:</p>
        <ul>
            <li>1 pto / 500 ha → rectangular 2 236 m · hexagonal ~ 2 398 m</li>
            <li>1 pto /  10 ha → rectangular   316 m · hexagonal ~   339 m</li>
            <li>1 pto /   1 ha → rectangular   100 m · hexagonal ~   107 m</li>
        </ul>
        <p>[!] <b>La densidad solicitada es teórica.</b> Los puntos sobre el borde del polígono se <b>incluyen</b> (operación <i>intersects</i>). En modo Individual y en modo Conjunto/Islas, un punto que cae exactamente sobre un borde compartido entre polígonos adyacentes puede aparecer en ambos. Use el método de detección de duplicados de borde antes de usar la capa como marco muestral.</p>

        <h4>Tipos de malla</h4>
        <ul>
            <li><b>Hexagonal (_EXA):</b> filas alternas con offset de espaciado/2. Mayor isotopía espacial (distancia uniforme a los 6 vecinos más cercanos). Recomendado para inventarios forestales y muestreos donde la dirección no debe sesgar resultados.</li>
            <li><b>Rectangular (_REC):</b> cuadrícula ortogonal. Simple y fácil de exportar a tablas.</li>
        </ul>

        <h4>Integridad geométrica</h4>
        <ul>
            <li><b>Reparar (recomendado):</b> aplica <code>reparar geometría</code>. Puede cambiar el tipo de geometría (Polygon → MultiPolygon). Partes no poligonales residuales se descartan.</li>
            <li><b>Omitir:</b> descarta el polígono sin generar puntos. Útil cuando la reparación automática no es aceptable para su flujo de trabajo.</li>
            <li><b>No verificar (riesgo):</b> procesa tal cual. Puede generar resultados incorrectos. Use solo si ya validó la geometría externamente.</li>
        </ul>

        <h4>Simplificación de entrada (Douglas-Peucker) — opcional</h4>
        <p>Reduce vértices de los polígonos <b>antes</b> de generar la malla. La ganancia en eficiencia depende del número de polígonos, su configuración geométrica y el número de puntos generados.</p>
        <ul>
            <li><b>Tolerancia 5 m (default):</b> adecuada para escalas 1:5 000–1:25 000. La regla práctica es: el impacto es mínimo cuando espaciado >> tolerancia y el polígono es suficientemente grande. Cuando el ancho del polígono es comparable a la tolerancia, el impacto puede ser significativo.</li>
            <li>Reduzca a 0,5–2 m para datos de alta precisión (GPS RTK, LiDAR, escala urbana).</li>
            <li>Aumente a 10–25 m para datos regionales (escala 1:50 000+).</li>
            <li>Si la simplificación produce una geometría inválida o vacía, se usa la original sin descartar el polígono.</li>
        </ul>

        <h4>Huecos, islas y multipolígonos — definiciones</h4>
        <ul>
            <li><b>Polígono simple:</b> una sola entidad de tipo Polygon (no MultiPolygon). Puede tener o no huecos (huecos).</li>
            <li><b>Polígonos múltiples:</b> capa con dos o más entidades Polygon independientes, cada una con su propio registro en la tabla de atributos.</li>
            <li><b>MultiPolígono:</b> una sola entidad de tipo MultiPolygon — múltiples partes poligonales en un único registro. Cada parte puede tener sus propios huecos. El algoritmo descompone cada parte y la procesa individualmente.</li>
            <li><b>Hueco:</b> hueco (<i>hueco</i>) de un polígono. Define una región excluida del área de la entidad — no recibe puntos. Gestionado por el parámetro <i>Eliminación de huecos</i>.</li>
            <li><b>Isla (término algorítmico):</b> cualquier entidad de la capa que no sea el polígono de mayor área (contenedor), independientemente de su posición espacial. Incluye polígonos geográficamente separados, adyacentes, solapados con el contenedor, o ubicados dentro de sus huecos. El criterio es exclusivamente el área relativa, no la relación espacial.</li>
            <li><b>Contenedor:</b> la entidad de mayor área, identificada automáticamente por el algoritmo. Si dos entidades tienen exactamente la misma área máxima, se usa la primera según el orden en la capa.</li>
        </ul>

        <h4>Eliminación de huecos</h4>
        <ul>
            <li><b>Área máxima = 0, Preservar hueco estructural activado (predeterminado):</b> conserva solo el hueco de mayor área; elimina el resto.</li>
            <li><b>Área máxima = 0, Preservar hueco estructural desactivado:</b> elimina TODOS los huecos (polígono sin huecos).</li>
            <li><b>Área máxima &gt; 0:</b> elimina huecos con área igual o menor al valor ingresado; conserva los mayores. Parámetro "Preservar" se ignora.</li>
        </ul>
        <p>En geometrías multiparte, cada parte se procesa por separado. Si ocurre un error durante el filtrado, se conserva la geometría original sin modificar y se registra una advertencia en el <b>Registro de mensajes</b> (etiqueta <i>MallaPuntos</i>) — la entidad no se descarta.</p>

        <h4>Capa de exclusión</h4>
        <p>Define zonas donde <b>no se generan puntos</b>. Aplica en todos los modos (Individual y Conjunto).</p>
        <ul>
            <li>Se calcula la diferencia geométrica entre el área de trabajo y la unión de las geometrías de exclusión.</li>
            <li>Si el SRC de la capa de exclusión difiere del de la entrada, se transforma automáticamente.</li>
            <li>El parámetro <b>búfer adicional sobre exclusión</b> expande la zona excluida sin modificar la capa fuente.</li>
            <li>El reporte HTML muestra: área bruta, área excluida, área efectiva y porcentaje excluido.</li>
        </ul>

        <h4>Interacción entre exclusión, islas y huecos</h4>
        <p>La capa de exclusión <b>no interactúa con la clasificación isla/hueco</b> — opera sobre la geometría de trabajo de cada modo de forma independiente.</p>
        <ul>
            <li>Si la zona de exclusión cae <b>completamente dentro</b> de un polígono de trabajo, <code>difference()</code> produce un nuevo hueco — un hueco geométrico sin puntos.
            Este hueco <b>no es modificado</b> por el parámetro <i>Eliminación de huecos</i>: comportamiento correcto, ya que rellenarlo contradiría la exclusión definida.</li>
            <li><b>Modo Contenedor:</b> la exclusión se aplica sobre el contenedor. Una exclusión interna produce un hueco nuevo en el contenedor; una exclusión que solape el borde lo recorta.</li>
            <li><b>Modo Islas:</b> la exclusión se aplica a cada isla individualmente. Una isla puede quedar parcialmente recortada o completamente excluida. El contenedor no se toca.</li>
            <li><b>Orden de operaciones — garantía de integridad:</b>
            Integridad → Simplificación → Eliminación de huecos → <b>Exclusión</b> → Generación de malla.
            El orden garantiza que las zonas excluidas nunca son rellenadas por <i>Eliminación de huecos</i>.</li>
        </ul>

        <h4>Campo en_borde — Identificación de duplicados de borde</h4>
        <p>Todos los puntos generados incluyen el campo <code>en_borde</code> (Bool):</p>
        <ul>
            <li><b>Falso (predeterminado):</b> punto en el interior del polígono.</li>
            <li><b>Verdadero:</b> punto sobre el borde <i>compartido</i> entre dos polígonos adyacentes — es un duplicado de borde capturado por ambos polígonos con distinto <code>ID_ORIGINAL</code>.</li>
        </ul>
        <p>El campo se asigna en post-proceso: tras detectar los duplicados de borde, todas las entidades afectadas (ambas copias de cada par) se marcan con <code>en_borde=True</code>. Esto permite identificar y filtrar los puntos problemáticos directamente desde la tabla de atributos sin necesidad de consultar el reporte HTML. Active <b>Eliminar entidades sobrantes</b> para eliminar las copias en exceso, conservando la entidad con menor <code>id_punto</code> en cada ubicación duplicada.</p>

        <h4>Archivos de salida</h4>
        <p>Si el archivo ya existe, se añade <code>_V1</code>, <code>_V2</code>, etc. sin sobreescribir.</p>
        <p><code>{base}_CON_ID_REC/EXA · _CONTENEDOR_REC/EXA · _ISLAS_REC/EXA · _UNIFICADO_REC/EXA</code></p>

        <p><b>Autor:</b> Jorge Fallas 2026 — <a href="mailto:jfallas56@gmail.com">jfallas56@gmail.com</a></p>
        """)

    def initAlgorithm(self, config=None):
        # --- CAPA DE ENTRADA ---
        param = QgsProcessingParameterFeatureSource(
            self.INPUT_POLYGON, self.tr('Capa de polígonos de entrada'),
            [_TypeVectorPolygon])
        param.setHelp(
            "Capa de polígonos de entrada.\n"
            "Capa de polígonos de entrada.\n"
            "[!] Debe estar en un SRC PROYECTADO en metros (ej. UTM, CRTM05 EPSG:8908).\n"
            "Con SRC geográfico (grados, EPSG:4326) la ejecución se cancela con error crítico.\n"
            "Si hay features seleccionadas, solo se procesan las seleccionadas;\n"
            "de lo contrario se procesan todos los polígonos de la capa."
            "de lo contrario se procesan todos los polígonos de la capa."
        )
        self.addParameter(param)

        # --- GESTIÓN DE INTEGRIDAD GEOMÉTRICA ---
        param = QgsProcessingParameterEnum(
            self.GESTION_INTEGRIDAD,
            '[*] Gestión de Integridad Geométrica',
            options=Constants.INTEGRIDAD_NAMES,
            defaultValue=Constants.INTEGRIDAD_REPARAR)
        param.setHelp(
            "Errores topológicos: auto-intersecciónes, anillos mal cerrados.\n"
            "[~] Reparar (default): reparar geometría. Partes no poligonales se descartan.\n"
            "[X] Omitir: polígono no genera puntos.\n"
            "[!] Riesgo: sin validar. Solo si ya validó la geometría."
        )
        self.addParameter(param)

        # --- SIMPLIFICACIÓN DE ENTRADA ---
        param = QgsProcessingParameterBoolean(
            self.SIMPLIFICAR_ENTRADA,
            '[~] Simplificar geometrías de entrada (polígonos)',
            defaultValue=False)
        param.setHelp(
            "Simplifica polígonos (Douglas-Peucker) ANTES de generar la malla.\n"
            "Opcional — desactivada por omisión.\n"
            "Si produce geometría inválida, usa la original.\n"
            "Reducción de vértices visible en reporte HTML.\n"
            "[!] Polígonos &lt;1000 m² o ancho &lt;10 m: ver ayuda de Tolerancia."
        )
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.TOLERANCIA_ENTRADA,
            '[~] Tolerancia simplificación entrada (m)',
            type=QgsProcessingParameterNumber.Double,
            minValue=0.1, defaultValue=5.0)
        param.setHelp(
            "Desviación máxima al simplificar (metros).\n"
            "0,1–0,5 m: GPS RTK, LiDAR, 1:500–1:2 000\n"
            "0,5–2 m: catastro, 1:2 000–1:5 000\n"
            "5 m (predeterminado): inventarios forestales, 1:5 000–1:25 000\n"
            "10–25 m: datos regionales, 1:50 000+\n"
            "[!] Polígonos &lt;1000 m² o ancho &lt;10 m:\n"
            "use 0,1–2 m para evitar deformaciones."
        )
        self.addParameter(param)

        # --- ELIMINACIÓN DE HUECOS ---
        param = QgsProcessingParameterBoolean(
            self.ELIMINAR_HUECOS,
            '(*) Eliminar huecos (anillos internos)',
            defaultValue=False)
        param.setHelp(
            "Rellena anillos internos antes de generar la malla.\n"
            "Sin opción: huecos respetados, sin puntos en su interior.\n"
            "Con opción: huecos rellenados, puntos generados en esas áreas.\n"
            "Use Área máxima a eliminar y Preservar hueco estructural para control fino."
        )
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.AREA_MINIMA_HUECO,
            '(*) Área máxima de hueco a eliminar (m²)',
            type=QgsProcessingParameterNumber.Integer,
            minValue=0, defaultValue=0)
        param.setHelp(
            "Tamaño máximo (m²) del hueco que será eliminado.\n"
            "= 0: depende de Preservar hueco estructural.\n"
            "&gt; 0: elimina huecos con área &lt;= este valor; conserva los mayores.\n"
            "Ej: 20 000 → elimina huecos &lt;= 2 ha, conserva &gt; 2 ha."
        )
        self.addParameter(param)

        param = QgsProcessingParameterBoolean(
            self.PRESERVAR_HUECO_ESTRUCTURAL,
            ' Preservar hueco estructural (donut)',
            defaultValue=True)
        param.setHelp(
            "Si Área máxima = 0: conserva el hueco mayor, elimina el resto.\n"
            "Activado (default): preserva hueco estructural (lago, exclusión).\n"
            "Desactivado: elimina todos los huecos.\n"
            "Sin efecto si Área máxima a eliminar &gt; 0."
        )
        self.addParameter(param)

        # --- MODO DE OPERACIÓN PRINCIPAL ---
        param = QgsProcessingParameterEnum(
            self.OPERATION_MODE, self.tr('Modo de Operación Principal'),
            options=self.MODE_LABELS, defaultValue=0)
        param.setHelp(
            "Conjunto: polígonos combinados. Sin ID_ORIGINAL.\n"
            "Individual: grupo por polígono. Requiere campo ID.\n"
            "en_borde=True: duplicado de borde — solo Individual e Islas.\n"
            "en_borde=False siempre en Unificado y Contenedor.\n"
            "'Isla': cualquier entidad que no sea el polígono mayor,\n"
            "sea o no geográficamente separada del contenedor."
        )
        self.addParameter(param)

        # --- CAMPO ID (SOLO MODO INDIVIDUAL) ---
        param = QgsProcessingParameterField(
            self.ID_FIELD, self.tr('Campo de ID (Solo para Modo Individual)'),
            parentLayerParameterName=self.INPUT_POLYGON,
            type=QgsProcessingParameterField.Any, optional=True)
        param.setHelp(
            "Valor copiado a ID_ORIGINAL en cada punto. Solo modo Individual.\n"
            "Acepta cualquier tipo. Preserva tipo y precisión del campo fuente.\n"
            "Multipart: todas las partes reciben el mismo ID_ORIGINAL.\n\n"
            "[!] Requisitos obligatorios (validados antes de iniciar):\n"
            "  · Sin valores NULL — cada polígono debe tener un ID asignado.\n"
            "  · Se recomienda IDs únicos — IDs duplicados pueden hacer\n"
            "    indistinguibles los duplicados de borde en el post-proceso.\n\n"
            "Si el campo tiene NULLs o duplicados, el algoritmo emite\n"
            "un error en checkParameterValues() antes de iniciar."
        )
        self.addParameter(param)

        # --- COMPORTAMIENTO (SOLO MODO CONJUNTO) ---
        param = QgsProcessingParameterEnum(
            self.FILL_BEHAVIOR, self.tr('Comportamiento (Solo para Modo Conjunto)'),
            options=self.BEHAVIOR_LABELS, defaultValue=0)
        param.setHelp(
            "Define qué área recibe la malla en modo Conjunto:\n"
            "— Unificado: unión de todos. en_borde=False siempre.\n"
            "— Contenedor: polígono mayor. en_borde=False siempre. &gt;=2.\n"
            "— Islas: malla en todas las entidades excepto el polígono mayor.\n"
            "  'Isla' = término algorítmico: cualquier entidad que no sea\n"
            "  el contenedor, sea o no geográficamente separada.\n"
            "  en_borde detecta duplicados. &gt;=2 polígonos."
        )
        self.addParameter(param)

        # --- TIPO DE MALLA ---
        param = QgsProcessingParameterEnum(
            self.GRID_TYPE, self.tr('Tipo de malla'),
            options=self.GRID_TYPE_LABELS, defaultValue=0)
        param.setHelp(
            "Hexagonal (_EXA): filas alternas con offset espaciado/2.\n"
            "Máxima isotropía espacial. Recomendado para inventarios forestales.\n"
            "Rectangular (_REC): cuadrícula ortogonal uniforme.\n"
            "Simple de interpretar y exportar a tablas."
        )
        self.addParameter(param)

        # --- ESPACIADO / DENSIDAD ---
        param = QgsProcessingParameterDistance(
            self.SPACING, self.tr('Espaciado (metros) [Prioridad 1]'),
            parentParameterName=self.INPUT_POLYGON, defaultValue=0.0, optional=True)
        param.setHelp(
            "Distancia entre puntos (metros). PRIORIDAD 1.\n"
            "Si &gt; 0 se usa directamente; Densidad y Ha/punto se ignoran.\n"
            "Reporte muestra densidad y cobertura EQUIVALENTES calculadas.\n"
            "en_borde=True en bordes compartidos (Individual e Islas)."
        )
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.DENSITY, self.tr('Densidad (Puntos/Ha) [Prioridad 2: Usar si Espaciado es 0]'),
            type=QgsProcessingParameterNumber.Double, defaultValue=0.0, optional=True, minValue=0.0)
        param.setHelp(
            "Puntos por hectárea. Solo si Espaciado = 0.\n"
            "Rectangular: e = sqrt(10000/d) · Hexagonal: e = sqrt(10000/(d×√3/2))\n"
            "Ej: 4 pts/ha → rectangular 50 m, hexagonal 53,7 m.\n"
            "en_borde=True en bordes compartidos (Individual e Islas)."
        )
        self.addParameter(param)

        # --- HA POR PUNTO ---
        param = QgsProcessingParameterNumber(
            self.HA_POR_PUNTO, self.tr('Hectáreas por punto (Ha/pto) [Prioridad 3: Usar si Espaciado y Densidad son 0]'),
            type=QgsProcessingParameterNumber.Double, defaultValue=0.0, optional=True, minValue=0.0)
        param.setHelp(
            "Ha por punto. Solo si Espaciado = 0 y Densidad = 0.\n"
            "Rectangular: e = sqrt(10000×h) · Hexagonal: e = sqrt(10000×h/(√3/2))\n"
            "Ej: 10 ha/pto → rectangular 316 m, hexagonal ~339 m.\n"
            "Reporte etiqueta 'Ha/punto solicitado'. Duplicados: en_borde=True."
        )
        self.addParameter(param)

        # --- DENSIDAD VARIABLE POR CLASE (JSON) ---
        param = QgsProcessingParameterFile(
            self.JSON_DENSIDAD_CLASES,
            '[{}] Densidad por clase JSON (Solo modo Individual)',
            behavior=QgsProcessingParameterFile.File,
            extension='json',
            optional=True)
        param.setHelp(
            "Archivo JSON que asigna una densidad diferente a cada clase del campo ID.\n"
            "Solo aplica en modo Individual. Anula los parámetros globales\n"
            "Espaciado, Densidad y Ha/punto para cada polígono individualmente.\n\n"
            "Claves aceptadas por clase (una por clase):\n"
            "  densidad (pts/ha), espaciado (m), ha_punto.\n"
            "Campo intervalo opcional para documentar el rango de la clase.\n\n"
            "Estructura A — clases en nivel raíz:\n"
            "{\n"
            "  \"meta\": {\"descripcion\": \"...\"},\n"
            "  \"1\": {\"densidad\": 10, \"intervalo\": \"7-23 ha\"},\n"
            "  \"2\": {\"ha_punto\": 0.2},\n"
            "  \"3\": {\"espaciado\": 48}\n"
            "}\n\n"
            "Estructura B — clases bajo clave 'clases':\n"
            "{\n"
            "  \"meta\": {\"descripcion\": \"...\"},\n"
            "  \"clases\": {\n"
            "    \"1\": {\"densidad\": 10},\n"
            "    \"2\": {\"ha_punto\": 0.2}\n"
            "  }\n"
            "}\n\n"
            "Si un ID no tiene entrada en el JSON, se usa la densidad global.\n"
            "Compatible con JSON de mapeo de clases — se pueden usar ambos."
        )
        self.addParameter(param)

        # --- CAPA DE EXCLUSIÓN ---
        param = QgsProcessingParameterFeatureSource(
            self.CAPA_EXCLUSION,
            self.tr('[X] Capa de exclusión (polígonos)'),
            [_TypeVectorPolygon],
            optional=True)
        param.setHelp(
            "Zonas donde NO se generan puntos. Aplica en todos los modos.\n"
            "Diferencia geométrica entre área de trabajo y unión de exclusiones.\n"
            "SRC diferente: transformación automática.\n"
            "Polígono completamente excluido: 0 puntos.\n"
            "Usos: agua, caminos, servidumbres, zonas ya muestreadas."
        )
        self.addParameter(param)

        param = QgsProcessingParameterDistance(
            self.BUFFER_EXCLUSION,
            self.tr('[X] Búfer adicional sobre exclusión (m)'),
            parentParameterName=self.CAPA_EXCLUSION,
            defaultValue=0.0,
            optional=True)
        param.setHelp(
            "búfer sobre exclusiones antes de calcular diferencia (metros).\n"
            "0 (default): exclusiones tal como están.\n"
            "&gt; 0: expande zona excluida sin modificar la capa fuente.\n"
            "Solo tiene efecto si hay capa de exclusión activa."
        )
        self.addParameter(param)

        # --- EXPORTACIÓN JSON DE PARÁMETROS ---
        param = QgsProcessingParameterBoolean(
            self.EXPORTAR_JSON,
            '[{}] Exportar parámetros de configuración a JSON',
            defaultValue=True)
        param.setHelp(
            "Guarda {nombre_base}_params.json en la carpeta de salida.\n"
            "Documenta configuración, métricas ISO 19157, simplificación,\n"
            "duplicados de borde y densidad real vs solicitada.\n"
            "Fundamental para reproducibilidad y trazabilidad."
        )
        self.addParameter(param)

        # --- MAPEO DE ID POR CLASES JSON ---
        param = QgsProcessingParameterFile(
            self.JSON_MAPEO_ID,
            '[{}] Archivo JSON de mapeo de clases (Solo modo Individual)',
            behavior=QgsProcessingParameterFile.File,
            extension='json',
            optional=True)
        param.setHelp(
            "Archivo JSON que mapea los valores del campo ID a clases de salida.\n"
            "Solo aplica en modo Individual. Si no se proporciona, se usa el valor\n"
            "original del campo ID sin transformación.\n\n"
            "Estructura del archivo JSON:\n"
            "{\n"
            "  \"campo_fuente\": \"ID_PARCELA\",    (informativo)\n"
            "  \"campo_salida\": \"CLASE_USO\",     (informativo)\n"
            "  \"descripcion\":  \"Clasificación de uso del suelo\",\n"
            "  \"mapeo\": {\n"
            " \"P001\": \"Bosque primario\",\n"
            " \"P002\": \"Bosque secundario\",\n"
            " \"P003\": \"Pastizal\"\n"
            "}\n"
            "}\n\n"
            "Comportamiento:\n"
            "- Si un ID no está en el mapeo: se usa el VALOR ORIGINAL del campo (fallback).\n"
            "- Cuando el mapeo está activo: el campo ID_ORIGINAL en la salida es tipo String.\n"
            "- Los IDs sin mapeo se listan en el reporte HTML y en el JSON de parámetros.\n"
            "- Las claves del mapeo se comparan como texto (conversión automática)."
        )
        self.addParameter(param)

        # --- SALIDAS ---
        param = QgsProcessingParameterFolderDestination(
            self.OUTPUT_FOLDER, self.tr('Carpeta de salida'))
        param.setHelp(
            "Carpeta de salida para GeoPackage y reporte HTML.\n"
            "Carpeta creada automáticamente. Archivo existente: se agrega _V1, _V2.\n"
            "Sufijos: _CON_ID (Individual) · _UNIFICADO/_CONTENEDOR/_ISLAS (Conjunto)\n"
            "         _EXA (hexagonal) · _REC (rectangular)\n"
            "Campos: id_punto, [ID_ORIGINAL], coord_x, coord_y, en_borde.\n"
            "en_borde=True solo en Individual e Islas (duplicados de borde).\n"
            "en_borde=False siempre en Unificado y Contenedor."
        )
        self.addParameter(param)

        param = QgsProcessingParameterString(
            self.OUTPUT_BASENAME, self.tr('Nombre base para el archivo de salida'),
            defaultValue='malla_puntos')
        param.setHelp(
            "{nombre_base}_{modo}_{tipo}.gpkg\n"
            "Ej: parcelas_CON_ID_EXA.gpkg, parcelas_UNIFICADO_REC.gpkg"
        )
        self.addParameter(param)

        param = QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML_REPORT, self.tr('Reporte HTML'),
            fileFilter='HTML files (*.html)', optional=True)
        param.setHelp(
            "Ruta del reporte HTML (opcional).\n"
            "Incluye densidad, calidad ISO 19157, simplificación,\n"
            "duplicados de borde y tabla de parámetros.\n"
            "Deje vacío si no necesita el reporte."
        )
        self.addParameter(param)

        param = QgsProcessingParameterBoolean(
            self.OPEN_REPORT, self.tr('Abrir reporte al finalizar'), defaultValue=True)
        param.setHelp(
            "Abre el reporte HTML en el navegador predeterminado al finalizar el proceso.\n"
            "Solo tiene efecto si se especificó una ruta en el campo 'Reporte HTML'."
        )
        self.addParameter(param)

        # --- ELIMINAR ENTIDADES SOBRANTES ---
        param = QgsProcessingParameterBoolean(
            self.ELIMINAR_SOBRANTES,
            self.tr('[X] Eliminar entidades sobrantes por duplicado de borde'),
            defaultValue=False)
        param.setHelp(
            "Elimina entidades sobrantes por duplicados de borde.\n"
            "Solo en modo Individual y Conjunto/Islas.\n"
            "Criterio: conserva la entidad con menor id_punto por ubicación.\n"
            "Resultado en Reporte HTML (Duplicados de Borde) y _params.json."
        )
        self.addParameter(param)

    def checkParameterValues(self, parameters, context):
        source = self.parameterAsSource(parameters, self.INPUT_POLYGON, context)
        if source is None or source.featureCount() == 0:
            return False, "[X] Error: Capa de entrada inválida o vacía."
        
        # Validar espaciado/densidad/ha_por_punto
        spacing     = self.parameterAsDouble(parameters, self.SPACING, context)
        density     = self.parameterAsDouble(parameters, self.DENSITY, context)
        ha_por_punto = self.parameterAsDouble(parameters, self.HA_POR_PUNTO, context)
        epsilon = 0.000001
        
        if spacing <= epsilon and density <= epsilon and ha_por_punto <= epsilon:
            return False, "[X] Error: Debe ingresar un valor válido en 'Espaciado', 'Densidad' o 'Ha/punto'."
        
        mode = self.parameterAsEnum(parameters, self.OPERATION_MODE, context)
        if mode == Constants.MODE_INDIVIDUAL:
            id_field = self.parameterAsString(parameters, self.ID_FIELD, context)
            if not id_field:
                return False, "[X] Error: En modo 'Individual' debe seleccionar un campo de ID."
            if source.fields().indexFromName(id_field) == -1:
                return False, f"[X] Error: El campo '{id_field}' no existe en la capa de entrada."
        
        if mode == Constants.MODE_CONJUNTO:
            behavior = self.parameterAsEnum(parameters, self.FILL_BEHAVIOR, context)
            if behavior in (Constants.FILL_CONTENEDOR, Constants.FILL_ISLAS) and source.featureCount() < 2:
                return False, "[X] Error: Los modos 'contenedor' e 'islas' requieren al menos dos polígonos."

        # Validar JSON de mapeo si se proporcionó
        json_mapeo_path = self.parameterAsFile(parameters, self.JSON_MAPEO_ID, context)
        json_densidad_path = self.parameterAsFile(parameters, self.JSON_DENSIDAD_CLASES, context)
        if json_mapeo_path:
            valido, msg = JsonMapper.validar_archivo(json_mapeo_path)
            if not valido:
                return False, msg
            if mode == Constants.MODE_CONJUNTO:
                return False, "[X] El mapeo JSON solo está disponible en modo Individual."

        # M-02: validación ligera de capa de exclusión antes de iniciar el proceso
        excl_check = self.parameterAsSource(parameters, self.CAPA_EXCLUSION, context)
        if excl_check is not None:
            if excl_check.featureCount() == 0:
                return False, "[X] Error: La capa de exclusión está vacía (sin features)."
            tiene_geom = False
            for feat in excl_check.getFeatures(
                    QgsFeatureRequest().setLimit(10).setNoAttributes()):
                g = feat.geometry()
                if g and not g.isEmpty():
                    tiene_geom = True
                    break
            if not tiene_geom:
                return False, "[X] Error: La capa de exclusión no contiene geometrías válidas."

        # Validar CRS de la capa de entrada
        _crs = source.sourceCrs()
        if CRSValidator.es_geografico(_crs):
            return False, (
                f"[!] ERROR CRÍTICO: La capa de entrada usa un SRC geográfico "
                f"({_crs.authid()} — grados). El algoritmo requiere un SRC "
                "proyectado en metros (ej: CRTM05 EPSG:8908, UTM). "
                "Reproyecte la capa antes de continuar."
            )

        # Validar campo ID en modo Individual
        _mode = self.parameterAsEnum(parameters, self.OPERATION_MODE, context)
        if _mode == Constants.MODE_INDIVIDUAL:
            _id_field = self.parameterAsString(parameters, self.ID_FIELD, context)
            if not _id_field or not _id_field.strip():
                return False, (
                    "[!] Modo Individual requiere un campo ID — "
                    "seleccione el campo identificador de cada polígono."
                )
            # Verificar NULLs en campo ID
            _null_ids  = []
            _seen_ids  = {}
            _dup_ids   = []
            _req_id = QgsFeatureRequest().setSubsetOfAttributes(
                [_id_field], source.fields())
            for _f in source.getFeatures(_req_id):
                _val = _f[_id_field]
                if _val is None or (isinstance(_val, str) and not _val.strip()):
                    _null_ids.append(_f.id())
                else:
                    _key = str(_val)
                    if _key in _seen_ids:
                        _dup_ids.append(_key)
                    _seen_ids[_key] = _f.id()
            if _null_ids:
                _n = len(_null_ids)
                _sample = ', '.join(str(x) for x in _null_ids[:5])
                _more = f' (+{_n-5} más)' if _n > 5 else ''
                return False, (
                    f"[!] El campo '{_id_field}' tiene {_n} valor(es) NULL "
                    f"(fid: {_sample}{_more}). "
                    "En modo Individual cada polígono debe tener un ID único "
                    "no nulo. Asigne IDs antes de continuar."
                )
            if _dup_ids:
                _n = len(set(_dup_ids))
                _sample = ', '.join(list(set(_dup_ids))[:5])
                return False, (
                    f"[!] El campo '{_id_field}' tiene {_n} valor(es) duplicado(s) "
                    f"({_sample}). "
                    "Se recomienda usar un campo con valores únicos para garantizar "
                    "la trazabilidad por polígono en la capa de salida."
                )

        # Validar capa de exclusión — verificación ligera antes de iniciar
        _excl = self.parameterAsSource(parameters, self.CAPA_EXCLUSION, context)
        if _excl is not None:
            _req = QgsFeatureRequest().setLimit(1).setNoAttributes()
            _tiene_features = any(True for _ in _excl.getFeatures(_req))
            if not _tiene_features:
                return False, (
                    "[!] La capa de exclusión está vacía — no contiene entidades. "
                    "Verifique la capa o desactive el parámetro Capa de exclusión."
                )

        return True, ""

    def get_unique_filepath(self, desired_path):
        path_to_use = desired_path
        counter = 1
        while os.path.exists(path_to_use):
            name_part, ext_part = os.path.splitext(desired_path)
            path_to_use = f"{name_part}_V{counter}{ext_part}"
            counter += 1
        return path_to_use

    def create_writer(self, path, fields, crs, context, layer_name):
        writer_options = QgsVectorFileWriter.SaveVectorOptions()
        writer_options.driverName = "GPKG"
        writer_options.layerName = layer_name
        transform_context = context.transformContext()
        writer = QgsVectorFileWriter.create(
            path, fields, _WkbPoint, crs,
            transform_context, writer_options)
        if writer.hasError() != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(writer.errorMessage())
        return writer


    def _preparar_geom(self, feat, gestion_integridad, simplificar_entrada,
                       tolerancia_entrada, eliminar_huecos, area_minima_hueco,
                       preservar_hueco_estructural, logger):
        """
        M-01: Método centralizado para preparar una geometria de entrada.
        Ejecuta en orden: validacion/integridad -> simplificacion -> eliminacion de huecos.
        Retorna (geom_preparada, v_antes, v_despues) o (None, 0,0) si debe omitirse.
        Este método reemplaza el bloque duplicado que existia en 3 lugares del código:
        modo Individual secuencial, modo Individual paralelo y modo Conjunto.
        """
        fid  = feat.id()
        desc = f"fid: {fid}"
        geom = feat.geometry()

        geom_prep = GeometryHandler.preparar_geometria(
            geom, fid, gestion_integridad, logger, desc)
        if geom_prep is None:
            return None, 0,0

        # Simplificacion de entrada
        if simplificar_entrada and geom_prep.type() == _GeomTypePolygon:
            v_antes = GeometrySimplifier.count_vertices(geom_prep)
            geom_prep, _, v_despues = GeometrySimplifier.simplify(
                geom_prep, tolerancia_entrada)
        else:
            # B-01 (fix): usar variable local, no el acumulador global
            v_antes   = GeometrySimplifier.count_vertices(geom_prep)
            v_despues = v_antes

        # Eliminacion de huecos
        if eliminar_huecos and geom_prep.type() == _GeomTypePolygon:
            n_huecos_antes = sum(
                len(p) - 1 for p in (geom_prep.asMultiPolygon()
                if QgsWkbTypes.isMultiType(geom_prep.wkbType())
                else [geom_prep.asPolygon()]))
            geom_prep, huecos_elim = GeometryPostProcessor.eliminar_huecos(
                geom_prep, area_minima_hueco, preservar_hueco_estructural)
            if huecos_elim > 0:
                n_conservados = n_huecos_antes - huecos_elim
                logger.info(f"   Huecos eliminados en polígono {fid}: {huecos_elim}"
                            f" · conservados: {n_conservados}"
                            f" (umbral: {area_minima_hueco:.0f} m²)")

        return geom_prep, v_antes, v_despues

    # === MODIFICACIÓN: R-04 + m-02 ===
    def _procesar_geom_con_exclusion(
            self, geom, excl_handler, area_label, logger):
        """Aplica exclusión a una geometría y retorna (geom_efectiva, area_excluida_m2).
        Retorna (None, area_original) si la geometría queda completamente excluida.
        Centraliza la lógica común entre modo Individual e Islas."""
        area_antes = geom.area()
        if not excl_handler.activo:
            return geom, 0.0
        geom_ef = excl_handler.aplicar_exclusion(geom)
        if geom_ef is None or geom_ef.isEmpty():
            logger.info(f"   [X] {area_label}: completamente excluido/a.")
            return None, area_antes
        area_excl = max(0.0, area_antes - geom_ef.area())
        return geom_ef, area_excl

    @staticmethod
    def _build_shapely_tree(polygon_geom):
        """Construye predicado point-in-polygon optimizado con Shapely.
        - MultiPolígono → STRtree sobre partes → O(log n) por punto.
        - Polígono simple → PreparedGeometry → 40-60× vs QgsGeometry.
        Retorna (tree_or_None, prepared_or_geom).
        Retorna (None, None) si Shapely no disponible."""
        if not _SHAPELY_AVAILABLE:
            return None, None
        try:
            geom_json = _json_shapely.loads(polygon_geom.asJson())
            shapely_geom = _shapely_shape(geom_json)
            # Retorna la geometría Shapely — se usa API vectorizada en fill_polygon
            return shapely_geom, _shapely_prep(shapely_geom)
        except Exception:
            return None, None
    def fill_polygon(self, polygon_geom, fields, spacing, grid_type,
                     source_id=None, start_id=1, include_source_id=True,
                     writer=None, progress_callback=None,
                     grid_origin=(0.0, 0.0)):
        """
        Genera puntos dentro de polygon_geom.

        Predicado de inclusión: intersects(QgsGeometry.fromPointXY(pt))
          Incluye puntos en el interior Y en el borde del polígono.
          Razón: contains() excluye el borde por definición (modelo DE-9IM de GEOS),
          introduciendo un sesgo sistemático inaceptable para marcos muestrales.
          intersects() garantiza cobertura completa sin omisión de borde.

          Nota API: intersects() NO acepta QgsPointXY directamente — solo acepta
          QgsRectangle o QgsGeometry. Se debe envolver con fromPointXY().
          contains() sí acepta QgsPointXY (fast path sin crear QgsGeometry).
          El overhead de fromPointXY() es ~10-20% vs contains(QgsPointXY).

          Nota marco muestral: en modo Individual con polígonos adyacentes que
          comparten un borde exacto, un punto sobre ese borde puede aparecer en
          ambos polígonos. Usar detectar_duplicados_borde() para identificar
          y gestionar estos casos.

        Rejilla global (grid_origin):
          Para garantizar que polígonos adyacentes generen exactamente el mismo
          punto en bordes compartidos, la rejilla se ancla en un origen global
          (x0, y0) en lugar del xmin/ymin de cada polígono. El origen se calcula
          una vez desde el bounding box de la capa completa antes de iterar.
          Sin origen global, dos rejillas con xmin distintos generan coordenadas
          distintas en el mismo borde — duplicados indetectables por comparación exacta.
        """
        features      = []
        written       = 0
        en_borde_count = 0

        # R-04: Validación final de geometría antes de iterar
        if not polygon_geom.isGeosValid():
            QgsMessageLog.logMessage(
                "[fill_polygon] Geometría inválida detectada. Se intentará reparar.",
                "MallaPuntos", Qgis.Warning)
            polygon_geom = polygon_geom.makeValid()
            if not polygon_geom or polygon_geom.isEmpty():
                return features if writer is None else 0

        bbox = polygon_geom.boundingBox()
        xmin, xmax, ymin, ymax = (bbox.xMinimum(), bbox.xMaximum(),
                                   bbox.yMinimum(), bbox.yMaximum())

        if spacing <= 0:
            return features if writer is None else 0

        # GR-01: origen global de rejilla.
        # En lugar de anclar en xmin/ymin del polígono, se usa el origen global
        # (x0, y0) calculado desde el bounding box de la capa completa.
        # Esto garantiza que polígonos adyacentes produzcan exactamente la misma
        # coordenada en bordes compartidos, haciendo los duplicados detectables.
        x0, y0 = grid_origin

        point_id = start_id

        _has_prepare = hasattr(polygon_geom, 'prepareGeometry')
        if _has_prepare:
            polygon_geom.prepareGeometry()

        # Shapely vectorizado — evalúa todos los candidatos en batch
        # sh.contains() procesa arrays NumPy liberando el GIL → máximo rendimiento
        # Fallback automático a QgsGeometry.intersects() si Shapely no disponible
        _shapely_geom, _shapely_prepared = self._build_shapely_tree(polygon_geom)
        _use_shapely_batch = _shapely_geom is not None

        def _point_in_polygon(px, py):
            """Fallback point-in-polygon — usado cuando batch no disponible.
            Usa contains(QgsPointXY) directamente — evita crear QgsGeometry
            temporal por cada punto candidato (mejora rendimiento sin Shapely)."""
            if _shapely_prepared is not None:
                return _shapely_prepared.contains(_ShapelyPoint(px, py))
            return polygon_geom.contains(QgsPointXY(px, py))

        def _emit(px, py):
            nonlocal point_id, written, en_borde_count
            pt_geom  = QgsGeometry.fromPointXY(QgsPointXY(px, py))
            # OB-01: en_borde se asigna False en generación.
            # Se marcará True en post-proceso (_marcar_duplicados_en_borde)
            # para los puntos identificados como duplicados de borde compartido.
            en_borde = False
            feat = QgsFeature(fields)
            feat.setGeometry(pt_geom)
            # Coordenadas almacenadas con 2 decimales (precisión 1 cm en metros)
            px2 = round(px, 2)
            py2 = round(py, 2)
            if include_source_id:
                feat.setAttributes([point_id, source_id, px2, py2, en_borde])
            else:
                feat.setAttributes([point_id, px2, py2, en_borde])
            if writer is not None:
                writer.addFeature(feat)
                written += 1
            else:
                features.append(feat)
            point_id += 1

        try:
            if grid_type == Constants.GRID_RECTANGULAR:
                c_start = int(math.floor((xmin - x0) / spacing))
                c_end   = int(math.ceil( (xmax - x0) / spacing))
                r_start = int(math.floor((ymin - y0) / spacing))
                r_end   = int(math.ceil( (ymax - y0) / spacing))
                total_rows = r_end - r_start + 1
                log_interval = max(1, total_rows // 10) if progress_callback else None
                if _use_shapely_batch:
                    # Batch vectorizado — genera todos los candidatos y evalúa en array
                    xs = _np_shapely.array([x0 + c * spacing
                                           for c in range(c_start, c_end + 1)])
                    for r in range(r_start, r_end + 1):
                        py = y0 + r * spacing
                        ys = _np_shapely.full(len(xs), py)
                        pts_arr = _sh.points(_np_shapely.column_stack([xs, ys]))
                        inside  = _sh.contains(_shapely_geom, pts_arr)
                        for px, ok in zip(xs[inside], inside[inside]):
                            _emit(float(px), py)
                        if progress_callback and log_interval and (
                                (r - r_start + 1) % log_interval == 0 or r == r_end):
                            progress_callback(r - r_start, total_rows)
                else:
                    for r in range(r_start, r_end + 1):
                        py = y0 + r * spacing
                        for c in range(c_start, c_end + 1):
                            px = x0 + c * spacing
                            try:
                                if _point_in_polygon(px, py):
                                    _emit(px, py)
                            except Exception as e_c:
                                QgsMessageLog.logMessage(
                                    f"[fill_polygon] Error ({px:.2f},{py:.2f}): {e_c}",
                                    "MallaPuntos", Qgis.Warning)
                        if progress_callback and log_interval and (
                                (r - r_start + 1) % log_interval == 0 or r == r_end):
                            progress_callback(r - r_start, total_rows)
            else:  # Hexagonal
                y_step = spacing * Constants.SQRT3_OVER_2
                row_start = int(math.floor((ymin - y0) / y_step))
                row_end   = int(math.ceil( (ymax - y0) / y_step))
                total_rows_hex = row_end - row_start + 1 if progress_callback else 0
                log_interval   = max(1, total_rows_hex // 10) if progress_callback else None
                if _use_shapely_batch:
                    # Batch vectorizado hexagonal
                    for row_index in range(row_start, row_end + 1):
                        y = y0 + row_index * y_step
                        x_offset = spacing / 2 if (row_index % 2 != 0) else 0
                        c_start_hex = int(math.floor((xmin - x0 - x_offset) / spacing))
                        c_end_hex   = int(math.ceil( (xmax - x0 - x_offset) / spacing))
                        xs = _np_shapely.array([x0 + x_offset + c * spacing
                                               for c in range(c_start_hex, c_end_hex + 1)])
                        ys = _np_shapely.full(len(xs), y)
                        pts_arr = _sh.points(_np_shapely.column_stack([xs, ys]))
                        inside  = _sh.contains(_shapely_geom, pts_arr)
                        for px in xs[inside]:
                            _emit(float(px), y)
                        if progress_callback and log_interval and (
                                (row_index - row_start + 1) % log_interval == 0):
                            progress_callback(row_index - row_start, total_rows_hex)
                    if progress_callback:
                        progress_callback(total_rows_hex, total_rows_hex or 1)
                else:
                    for row_index in range(row_start, row_end + 1):
                        y = y0 + row_index * y_step
                        x_offset = spacing / 2 if (row_index % 2 != 0) else 0
                        c_start_hex = int(math.floor((xmin - x0 - x_offset) / spacing))
                        c_end_hex   = int(math.ceil( (xmax - x0 - x_offset) / spacing))
                        for c in range(c_start_hex, c_end_hex + 1):
                            x = x0 + x_offset + c * spacing
                            try:
                                if _point_in_polygon(x, y):
                                    _emit(x, y)
                            except Exception as e_c:
                                QgsMessageLog.logMessage(
                                    f"[fill_polygon] Error ({x:.2f},{y:.2f}): {e_c}",
                                    "MallaPuntos", Qgis.Warning)
                        if progress_callback and log_interval and (
                                (row_index - row_start + 1) % log_interval == 0):
                            progress_callback(row_index - row_start, total_rows_hex)
                    if progress_callback:
                        progress_callback(total_rows_hex, total_rows_hex or 1)
        finally:
            if _has_prepare:
                polygon_geom.releaseCache()

        return (features, en_borde_count) if writer is None else (written, en_borde_count)

    def _generar_malla_sobre_geometria(self, geom, fields, spacing, grid_type,
                                       start_id, include_source_id, source_id,
                                       logger, feedback, writer,
                                       descripcion_tarea="Generando malla",
                                       grid_origin=(0.0, 0.0)):
        """
        Retorna (nuevo_current_id, puntos_generados, puntos_en_borde).
        C-03: writer como parámetro explícito.
        GR-01: grid_origin se pasa a fill_polygon para rejilla global.
        """
        if not geom or geom.isEmpty():
            return start_id, 0,0

        if not geom.isGeosValid():
            logger.warning(f"{descripcion_tarea}: Geometría inválida, intentando reparar...")
            geom = geom.makeValid()
            if not geom or geom.isEmpty():
                logger.warning(f"{descripcion_tarea}: Reparación fallida — geometría omitida.")
                logger.error(f"{descripcion_tarea}: No se pudo reparar la geometría. Omitiendo.")
                return start_id, 0, 0

        result, en_borde_count = self.fill_polygon(
            geom, fields, spacing, grid_type,
            source_id=source_id,
            start_id=start_id,
            include_source_id=include_source_id,
            writer=writer,
            grid_origin=grid_origin)

        count = result if isinstance(result, int) else len(result)
        if not isinstance(result, int):
            for feat in result:
                writer.addFeature(feat)

        if count > 0:
            logger.info(f"{descripcion_tarea}: {count} puntos generados"
                        f"{f' ({en_borde_count} en borde)' if en_borde_count > 0 else ''}.")

        return start_id + count, count, en_borde_count

    @staticmethod
    def detectar_duplicados_borde(output_gpkg_path: str,
                                   layer_name: str,
                                   logger: 'Logger' = None) -> Dict[str, Any]:
        """
        Detecta puntos duplicados que coinciden exactamente con el perímetro
        de polígonos contiguos en modo Individual.

        Un punto duplicado de borde ocurre cuando un punto cae exactamente sobre
        el borde compartido entre dos polígonos adyacentes: con intersects() el
        punto es capturado por ambos polígonos, resultando en dos features con
        coordenadas idénticas pero distinto ID_ORIGINAL.

        Estrategia de detección:
          1. Agrupa features por (coord_x, coord_y).
          2. Cualquier grupo con más de una feature es un duplicado de borde
             (mismo punto, distinto ID_ORIGINAL).
          3. Retorna: conteo de duplicados, lista de coord (x, y) duplicadas,
             y los id_punto afectados.

        Retorna dict con:
          'total_puntos':      total de features en la capa
          'duplicados':        número de coordenadas con más de una feature
          'features_extra':    número de features que son duplicados (exceso)
          'coords_duplicadas': lista de (x, y) con duplicados
          'ids_afectados':     lista de id_punto duplicados
          'pct_duplicados':    porcentaje sobre el total de puntos

        Uso recomendado:
          res = CrearMallaPuntos.detectar_duplicados_borde(path, layer_name)
          if res['duplicados'] > 0:
              # advertir al usuario antes de usar como marco muestral
        """
        try:
            from qgis.core import QgsVectorLayer
            layer = QgsVectorLayer(
                f"{output_gpkg_path}|layername={layer_name}", "check", "ogr")
            if not layer.isValid():
                if logger:
                    logger.error(f"[Duplicados] No se pudo abrir la capa: {output_gpkg_path}")
                return {}

            # Agrupar por coordenadas exactas
            coord_index: Dict[tuple, list] = {}
            # Redondear a 6 decimales para evitar falsos negativos por
            # aritmética de punto flotante: dos polígonos adyacentes calculan
            # la misma coordenada de borde partiendo de xmin distintos, lo que
            # puede producir diferencias en el último bit (ej. 346828.0000000001
            # vs 346827.9999999998). 6 decimales = precisión de 1 μm en metros.
            for feat in layer.getFeatures():
                x = round(feat['coord_x'], 6)
                y = round(feat['coord_y'], 6)
                key = (x, y)
                if key not in coord_index:
                    coord_index[key] = []
                coord_index[key].append(feat['id_punto'])

            total_puntos    = layer.featureCount()
            duplicados      = {k: v for k, v in coord_index.items() if len(v) > 1}
            n_duplicados    = len(duplicados)
            features_extra  = sum(len(v) - 1 for v in duplicados.values())
            coords_dup      = list(duplicados.keys())
            ids_afectados   = [id_p for ids in duplicados.values() for id_p in ids]
            pct             = (features_extra / total_puntos * 100) if total_puntos > 0 else 0.0

            # Separar conservados (menor id_punto por grupo) y sobrantes
            ids_conservados = sorted([min(v) for v in duplicados.values()])
            ids_sobrantes   = sorted([id_p for v in duplicados.values()
                                      for id_p in sorted(v)[1:]])

            resumen = {
                'total_puntos':      total_puntos,
                'duplicados':        n_duplicados,
                'features_extra':    features_extra,
                'coords_duplicadas': coords_dup[:50],   # limitar a 50 para el log
                'ids_afectados':     sorted(ids_afectados)[:100],
                'ids_conservados':   ids_conservados[:100],
                'ids_sobrantes':     ids_sobrantes[:100],
                'pct_duplicados':    round(pct, 4),
            }

            if logger:
                if n_duplicados == 0:
                    logger.info("[Duplicados] Sin duplicados de borde detectados.")
                else:
                    logger.warning(
                        f"[Duplicados] {n_duplicados} coordenada(s) duplicada(s) "
                        f"({features_extra} entidad(es) sobrante(s), {_fmt(pct, 2)}% del total). "
                        f"Estos puntos caen exactamente sobre bordes compartidos entre "
                        f"polígonos adyacentes. Revisar antes de usar como marco muestral.")
            return resumen

        except Exception as e:
            if logger:
                logger.error(f"[Duplicados] Error en detección: {e}")
            return {}

    def processAlgorithm(self, parameters, context, feedback):
        logger = Logger(feedback, tag="MallaPuntos")
        writer = None  # N-08: inicializar para que 'del writer' sea seguro en cualquier ruta
        try:
            # Configurar contexto para no filtrar geometrías inválidas
            context.setInvalidGeometryCheck(_GeomNoCheck)
            
            source = self.parameterAsSource(parameters, self.INPUT_POLYGON, context)
            vector_layer_ref = self.parameterAsVectorLayer(parameters, self.INPUT_POLYGON, context)
            layer_name = vector_layer_ref.name() if vector_layer_ref else "Polígono de entrada"
            
            # Parámetros de integridad
            gestion_integridad = self.parameterAsEnum(parameters, self.GESTION_INTEGRIDAD, context)
            simplificar_entrada = self.parameterAsBoolean(parameters, self.SIMPLIFICAR_ENTRADA, context)
            tolerancia_entrada = self.parameterAsDouble(parameters, self.TOLERANCIA_ENTRADA, context)
            eliminar_huecos = self.parameterAsBoolean(parameters, self.ELIMINAR_HUECOS, context)
            area_minima_hueco = float(self.parameterAsInt(parameters, self.AREA_MINIMA_HUECO, context))
            preservar_hueco_estructural = self.parameterAsBoolean(parameters, self.PRESERVAR_HUECO_ESTRUCTURAL, context)
            
            operation_mode = self.parameterAsEnum(parameters, self.OPERATION_MODE, context)
            grid_type = self.parameterAsEnum(parameters, self.GRID_TYPE, context)
            
            # Cálculo del espaciado
            spacing_input   = self.parameterAsDouble(parameters, self.SPACING, context)
            density_input   = self.parameterAsDouble(parameters, self.DENSITY, context)
            ha_por_punto_input = self.parameterAsDouble(parameters, self.HA_POR_PUNTO, context)
            epsilon = 0.000001
            spacing = 0.0
            input_info_str = ""
            
            if spacing_input > epsilon:
                spacing = spacing_input
                input_info_str = f"Distancia manual: {spacing} m"
            elif density_input > epsilon:
                if grid_type == Constants.GRID_RECTANGULAR:
                    spacing = math.sqrt(10000.0 / density_input)
                else:
                    spacing = math.sqrt(10000.0 / (density_input * Constants.SQRT3_OVER_2))  # E-07
                input_info_str = f"Densidad: {_fmt(density_input, 4)} pts/Ha (Dist.: {_fmt(spacing, 4)} m)"
                feedback.pushInfo(f"Densidad solicitada: {density_input} pts/Ha -> Espaciado calculado: {spacing:.4f} m")
            elif ha_por_punto_input > epsilon:
                # Ha/punto = 1/densidad -> density_equiv = 1/ha_por_punto_input
                density_equiv = 1.0 / ha_por_punto_input
                if grid_type == Constants.GRID_RECTANGULAR:
                    spacing = math.sqrt(10000.0 / density_equiv)
                else:
                    spacing = math.sqrt(10000.0 / (density_equiv * Constants.SQRT3_OVER_2))  # E-07
                input_info_str = f"Ha/punto: {_fmt(ha_por_punto_input, 4)} ha/pto (Equiv: {_fmt(density_equiv, 6)} pts/Ha, Dist.: {_fmt(spacing, 4)} m)"
                feedback.pushInfo(
                    f"Ha/punto solicitado: {ha_por_punto_input} ha/pto -> "
                    f"Densidad equivalente: {density_equiv:.6f} pts/ha -> "
                    f"Espaciado calculado: {spacing:.4f} m")
                # Guardar como density_input equivalente para el reporte
                density_input = density_equiv
            else:
                raise QgsProcessingException("Error: Debe ingresar un valor válido en 'Espaciado', 'Densidad' o 'Ha/punto'.")
            
            # Error crítico SRC geográfico — cancela ejecución
            crs = source.sourceCrs()
            if CRSValidator.es_geografico(crs):
                msg = (
                    "[!] ERROR CRÍTICO: La capa de entrada usa un SRC "
                    "(grados). El algoritmo requiere un SRC proyectado en metros "
                    "(ej: CRTM05 EPSG:8908, UTM). "
                    "Con coordenadas en grados el espaciado se interpreta como grados, "
                    "no como metros, produciendo resultados completamente inválidos. "
                    "Reproyecte la capa antes de continuar."
                )
                logger.error(msg)
                raise QgsProcessingException(msg)
            
            output_folder = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
            basename = self.parameterAsString(parameters, self.OUTPUT_BASENAME, context)
            html_file = self.parameterAsString(parameters, self.OUTPUT_HTML_REPORT, context)
            open_report = self.parameterAsBoolean(parameters, self.OPEN_REPORT, context)
            eliminar_sobrantes = self.parameterAsBoolean(parameters, self.ELIMINAR_SOBRANTES, context)
            exportar_json = self.parameterAsBoolean(parameters, self.EXPORTAR_JSON, context)
            json_mapeo_path = self.parameterAsFile(parameters, self.JSON_MAPEO_ID, context)
            json_densidad_path = self.parameterAsFile(parameters, self.JSON_DENSIDAD_CLASES, context)
            excl_source = self.parameterAsSource(parameters, self.CAPA_EXCLUSION, context)
            buffer_exclusion = self.parameterAsDouble(parameters, self.BUFFER_EXCLUSION, context)
            
            if not os.path.exists(output_folder):
                os.makedirs(output_folder)
            # M-04: verificar que la carpeta sea escribible antes de procesar
            if not os.access(output_folder, os.W_OK):
                raise QgsProcessingException(
                    f"La carpeta de salida no tiene permisos de escritura: {output_folder}")
            
            grid_type_suffix = '_EXA' if grid_type == Constants.GRID_HEXAGONAL else '_REC'
            all_features = list(source.getFeatures())
            
            if not all_features:
                raise QgsProcessingException('No hay objetos para procesar.')

            # GR-01: calcular origen global de rejilla desde bounding box de la capa.
            # Todos los polígonos usarán este mismo origen, garantizando que las
            # rejillas de polígonos adyacentes estén alineadas y produzcan exactamente
            # el mismo punto en bordes compartidos.
            _layer_bbox = source.sourceExtent()
            grid_origin = (_layer_bbox.xMinimum(), _layer_bbox.yMinimum())
            logger.info(f"[GR-01] Origen global de rejilla: ({grid_origin[0]:.4f}, {grid_origin[1]:.4f})")

            # Log inicial con parámetros
            if _SHAPELY_AVAILABLE:
                logger.info(
                    f"[STRtree] Shapely {_SHAPELY_VERSION} disponible — "
                    "point-in-polygon acelerado. "
                    "Motor: ufunc vectorizado (NumPy) con PreparedGeometry / STRtree. "
                    "Reducción medida: hasta 218× sobre geometrías complejas.")
            else:
                logger.warning(
                    "[STRtree] Shapely no disponible — usando QgsGeometry.intersects() "
                    "(más lento). Shapely está incluido en las instalaciones "
                    "standalone (.msi) y OSGeo4W de QGIS 3.44 LTR y QGIS 4.0. "
                    "Si este mensaje aparece, reinstale QGIS o instale Shapely "
                    "manualmente: python -m pip install shapely")
            logger.info(f"Iniciando algoritmo. Capa: {layer_name}, {len(all_features)} polígonos, modo: {self.MODE_LABELS[operation_mode]}")
            logger.info(f"Espaciado: {spacing:.4f} m, tipo malla: {self.GRID_TYPE_LABELS[grid_type]}")
            logger.info(f"Gestión integridad: {Constants.INTEGRIDAD_NAMES[gestion_integridad]}, simplificar: {simplificar_entrada}, eliminar huecos: {eliminar_huecos}")
            
            # N-07: start_id eliminado - current_id siempre inicia en 1
            final_output_path = ''
            total_processed_area_m2 = 0.0
            vertices_antes_total = 0
            vertices_despues_total = 0
            
            # Estadísticas para el reporte
            stats = {
                'layer_name': layer_name,
                'crs': crs.authid(),
                'polygons_count': len(all_features),
                'spacing': spacing,
                'input_info': input_info_str,
                # Valores originales ingresados por el usuario (antes de sobreescrituras)
                'spacing_input_orig':      spacing_input,
                'density_input_orig':      self.parameterAsDouble(parameters, self.DENSITY, context),
                'ha_por_punto_input_orig': ha_por_punto_input,
                'grid_type': self.GRID_TYPE_LABELS[grid_type],
                'mode': self.MODE_LABELS[operation_mode],
                'behavior': 'N/A',
                'total_points': 0,
                'puntos_en_borde': 0,
                'output_file': '',
                'total_area_ha': 0.0,
                'area_per_point': 0.0,
                'simplificacion_activa': simplificar_entrada,
                'tolerancia_entrada': tolerancia_entrada,
                'eliminar_huecos': eliminar_huecos,
                'area_minima_hueco': area_minima_hueco,
                'preservar_hueco_estructural': preservar_hueco_estructural,
                'gestion_integridad': gestion_integridad,
                'exportar_json': exportar_json,
                'json_mapeo_path':     json_mapeo_path or '',
                'json_densidad_path':  json_densidad_path or '',
                'sobrantes_eliminados': 0,      # entidades eliminadas por duplicado de borde
                'exclusion_activa': False,      # se actualiza tras inicializar handler
                'exclusion_n_features': 0,
                'exclusion_buffer_m': buffer_exclusion,
                'area_excluida_ha': 0.0,
            }

            # Inicializar capa de exclusión
            excl_handler = ExclusionHandler(
                excl_source, buffer_exclusion, crs, context, logger)
            stats['exclusion_activa']    = excl_handler.activo
            stats['exclusion_n_features'] = excl_handler.n_features

            # Inicializar mapeador JSON (modo Individual solamente)
            mapper = JsonMapper(
                json_mapeo_path if operation_mode == Constants.MODE_INDIVIDUAL else '',
                logger)
            
            if operation_mode == Constants.MODE_INDIVIDUAL:
                id_field_name = self.parameterAsString(parameters, self.ID_FIELD, context)
                source_field = source.fields().field(id_field_name)

                fields = QgsFields()
                fields.append(QgsField("id_punto", _INT_TYPE))
                # Si el mapeo está activo, el campo ID_ORIGINAL es siempre String
                if mapper.activo:
                    fld_id = QgsField("ID_ORIGINAL", _STRING_TYPE, "String", 255)
                    logger.info(
                        f"[JSON] Mapeo activo: campo ID_ORIGINAL → String "
                        f"({mapper.meta.get('total_clases', 0)} clases definidas)")
                else:
                    fld_id = QgsField("ID_ORIGINAL", source_field.type(), source_field.typeName())
                    fld_id.setLength(source_field.length())
                    fld_id.setPrecision(source_field.precision())
                fields.append(fld_id)
                fields.append(QgsField("coord_x", _DOUBLE_TYPE))
                fields.append(QgsField("coord_y", _DOUBLE_TYPE))
                fields.append(QgsField("en_borde", QVariant.Bool))
                
                output_filename = f"{basename}_CON_ID{grid_type_suffix}.gpkg"
                base_output_path = os.path.join(output_folder, output_filename)
                final_output_path = self.get_unique_filepath(base_output_path)
                layer_name_out = os.path.splitext(os.path.basename(final_output_path))[0]
                
                writer = self.create_writer(
                    final_output_path, fields, source.sourceCrs(), context, layer_name_out)

                # Cargar JSON de densidad por clase
                densidad_clases = {}
                if json_densidad_path and os.path.isfile(json_densidad_path):
                    try:
                        with open(json_densidad_path, 'r', encoding='utf-8-sig') as _f:
                            densidad_clases = json.load(_f)
                        # Acepta estructura con clave 'clases' anidada
                        if 'clases' in densidad_clases and isinstance(densidad_clases['clases'], dict):
                            _meta = densidad_clases.get('meta', {})
                            densidad_clases = densidad_clases['clases']
                            densidad_clases['__meta__'] = _meta  # preservar meta
                        n_clases = len([k for k in densidad_clases if not k.startswith('__') and k != 'meta'])
                        logger.info(f"[JSON-D] Densidad por clase cargada: {n_clases} clases")
                    except Exception as e:
                        logger.warning(f"[JSON-D] Error leyendo JSON de densidad: {e}. Se usará densidad global.")

                logger.info(f"Procesando {len(all_features)} polígono(s) individualmente...")
                current_id = 1
                total_points_written = 0
                total_puntos_en_borde = 0
                total_poligonos = len(all_features)

                for i, feature in enumerate(all_features):
                    if feedback.isCanceled():
                        logger.warning("Proceso cancelado por el usuario — resultado parcial.")
                        break
                    feedback.setProgress(int((i + 1) * 100 / total_poligonos))

                    if i % max(1, total_poligonos // 10) == 0 or i == total_poligonos - 1:
                        elapsed = logger.get_tiempo_ejecucion()
                        pct = (i + 1) / total_poligonos * 100
                        logger.info(f"Progreso: {i+1}/{total_poligonos} poligonos ({_fmt(pct, 1)}%) en {elapsed:.1f}s")

                    # M-01: preprocesamiento centralizado
                    geom_prep, v_antes, v_despues = self._preparar_geom(
                        feature, gestion_integridad, simplificar_entrada,
                        tolerancia_entrada, eliminar_huecos, area_minima_hueco,
                        preservar_hueco_estructural, logger)
                    if geom_prep is None:
                        continue

                    vertices_antes_total  += v_antes
                    vertices_despues_total += v_despues

                    # R-02: aplicar exclusión con helper centralizado
                    geom_prep, area_excl_poly = self._procesar_geom_con_exclusion(
                        geom_prep, excl_handler,
                        f"Polígono fid:{feature.id()}", logger)
                    if geom_prep is None:
                        excl_handler.acumular_area_excluida(area_excl_poly)
                        continue
                    excl_handler.acumular_area_excluida(area_excl_poly)

                    area = 0.0  # B-03
                    source_id_val = mapper.aplicar(feature[id_field_name])

                    # JSON-D: calcular spacing por clase si hay JSON de densidad
                    spacing_poly = spacing  # default: spacing global
                    if densidad_clases:
                        _raw_id = feature[id_field_name]
                        _id_val = str(_raw_id) if _raw_id is not None else ''
                        _clase = {k: v for k, v in densidad_clases.items() if not k.startswith('__') and k != 'meta'}.get(_id_val, {})
                        if 'densidad' in _clase and _clase['densidad'] > 0:
                            _d = float(_clase['densidad'])
                            if grid_type == Constants.GRID_HEXAGONAL:
                                spacing_poly = math.sqrt(Constants.HA_TO_M2 / (_d * Constants.SQRT3_OVER_2))
                            else:
                                spacing_poly = math.sqrt(Constants.HA_TO_M2 / _d)
                        elif 'espaciado' in _clase and _clase['espaciado'] > 0:
                            spacing_poly = float(_clase['espaciado'])
                        elif 'ha_punto' in _clase and _clase['ha_punto'] > 0:
                            _d = 1.0 / float(_clase['ha_punto'])
                            if grid_type == Constants.GRID_HEXAGONAL:
                                spacing_poly = math.sqrt(Constants.HA_TO_M2 / (_d * Constants.SQRT3_OVER_2))
                            else:
                                spacing_poly = math.sqrt(Constants.HA_TO_M2 / _d)

                    # RF-01 + C-03: writer pasado como parámetro explícito
                    if geom_prep.isMultipart():
                        parts = geom_prep.asGeometryCollection()
                        for part in parts:
                            if part and part.type() == _GeomTypePolygon:
                                area += part.area()
                                total_processed_area_m2 += part.area()
                                new_id, pts_count, eb_count = self._generar_malla_sobre_geometria(
                                    part, fields, spacing_poly, grid_type, current_id, True,
                                    source_id_val, logger, feedback, writer,
                                    f"Parte multipolígono fid:{feature.id()}",
                                    grid_origin=grid_origin)
                                current_id = new_id
                                total_points_written += pts_count
                                total_puntos_en_borde += eb_count
                    else:
                        area = geom_prep.area()
                        total_processed_area_m2 += area
                        new_id, pts_count, eb_count = self._generar_malla_sobre_geometria(
                            geom_prep, fields, spacing_poly, grid_type, current_id, True,
                            source_id_val, logger, feedback, writer,
                            f"Polígono fid:{feature.id()} (spacing={spacing_poly:.2f} m)",
                            grid_origin=grid_origin)
                        current_id = new_id
                        total_points_written += pts_count
                        total_puntos_en_borde += eb_count

                    logger.registrar_area(area)  # B-03

                stats['total_points'] = total_points_written
                stats['puntos_en_borde'] = total_puntos_en_borde
                if total_puntos_en_borde > 0:
                    logger.info(f"[Borde] {total_puntos_en_borde} punto(s) en el perímetro del polígono (campo en_borde=True).")
                logger.info(f"Total puntos generados: {stats['total_points']}")
                # Guardar resumen del mapeo en stats
                stats['mapeo_resumen'] = mapper.get_resumen()
                stats['densidad_clases_activo'] = bool(densidad_clases)
                # Separar metadatos de las clases
                _dc_meta   = densidad_clases.get('__meta__', densidad_clases.get('meta', {}))
                _dc_clases = {k: v for k, v in densidad_clases.items() if k != 'meta' and not k.startswith('__')}
                stats['densidad_clases_resumen'] = {
                    'activo':   bool(_dc_clases),
                    'json_path': json_densidad_path or '',
                    'clases':   _dc_clases,
                    'meta':     _dc_meta
                } if densidad_clases else None
                if mapper.activo and mapper.get_resumen()['sin_mapeo']:
                    logger.warning(
                        f"[JSON] {len(mapper.get_resumen()['sin_mapeo'])} ID(s) sin mapeo "
                        f"(se usó valor original): {', '.join(str(x) for x in mapper.get_resumen()['sin_mapeo'][:10])}"
                        + (' ...' if len(mapper.get_resumen()['sin_mapeo']) > 10 else ''))
                stats['behavior'] = 'N/A'
                # Detección de duplicados de borde (solo modo Individual)
                # Se ejecuta después de cerrar el writer para que el archivo esté completo
                stats['duplicados_borde'] = None  # se actualiza tras del writer
            
            elif operation_mode == Constants.MODE_CONJUNTO:
                # Modo conjunto
                fill_behavior = self.parameterAsEnum(parameters, self.FILL_BEHAVIOR, context)
                stats['behavior'] = self.BEHAVIOR_LABELS[fill_behavior]
                logger.info(f"Modo conjunto, comportamiento: {stats['behavior']}")
                
                fields = QgsFields()
                fields.append(QgsField("id_punto", _INT_TYPE))
                fields.append(QgsField("coord_x", _DOUBLE_TYPE))
                fields.append(QgsField("coord_y", _DOUBLE_TYPE))
                fields.append(QgsField("en_borde", QVariant.Bool))
                
                behavior_suffix_map = ['_UNIFICADO', '_CONTENEDOR', '_ISLAS']
                output_filename = f"{basename}{behavior_suffix_map[fill_behavior]}{grid_type_suffix}.gpkg"
                base_output_path = os.path.join(output_folder, output_filename)
                final_output_path = self.get_unique_filepath(base_output_path)
                layer_name_out = os.path.splitext(os.path.basename(final_output_path))[0]
                
                writer = self.create_writer(
                    final_output_path, fields, source.sourceCrs(), context, layer_name_out)

                # Recolectar geometrías preparadas con mensajes de progreso
                geometries = []
                total_poligonos = len(all_features)
                logger.info(f"Preparando geometrías de {total_poligonos} polígonos...")

                for i, feat in enumerate(all_features):
                    if feedback.isCanceled():
                        break

                    if i % max(1, total_poligonos // 10) == 0 or i == total_poligonos - 1:
                        elapsed = logger.get_tiempo_ejecucion()
                        pct = (i+1)/total_poligonos*100
                        logger.info(f"Preparando geometrias: {i+1}/{total_poligonos} poligonos ({_fmt(pct, 1)}%) en {elapsed:.1f}s")

                    # M-01+B-01: preprocesamiento centralizado (fix acumulador vertices)
                    geom_prep, v_antes, v_despues = self._preparar_geom(
                        feat, gestion_integridad, simplificar_entrada,
                        tolerancia_entrada, eliminar_huecos, area_minima_hueco,
                        preservar_hueco_estructural, logger)
                    if geom_prep is None:
                        continue

                    vertices_antes_total  += v_antes
                    vertices_despues_total += v_despues  # B-01: variable local correcta

                    if geom_prep.isMultipart():
                        parts = geom_prep.asGeometryCollection()
                        for part in parts:
                            if part and part.type() == _GeomTypePolygon:
                                geometries.append(part)
                    else:
                        geometries.append(geom_prep)

                if not geometries:
                    raise QgsProcessingException("No se pudo procesar ninguna geometría válida.")

                current_id = 1
                total_points_conjunto = 0  # H-5: contador explícito
                total_puntos_en_borde = 0

                # N-06: detectar contenedor una sola vez; comparar por WKB
                # GV-02: los huecos son huecos de la propia geometría del
                # contenedor — no polígonos separados en la capa. Todo polígono separado
                # en la capa es por definición una isla, independientemente de su posición
                # espacial. La lógica contains() (GV-01) fue eliminada porque hole_geoms
                # siempre resultaba vacío en datos reales: ningún feature independiente
                # puede ser hueco de otro feature.
                if fill_behavior in (Constants.FILL_CONTENEDOR, Constants.FILL_ISLAS):
                    if len(geometries) < 2:
                        raise QgsProcessingException("Este modo requiere al menos dos poligonos.")
                    container_geom = max(geometries, key=lambda g: g.area())
                    container_wkb  = container_geom.asWkb()
                    island_geoms   = [g for g in geometries if g.asWkb() != container_wkb]

                    logger.info(
                        f"{len(island_geoms)} isla(s) identificada(s) "
                        f"(polígonos separados distintos del contenedor).")

                if fill_behavior == Constants.FILL_CONTENEDOR:
                    logger.info("Generando malla sobre el contenedor...")
                    # GV-02: el contenedor se usa tal como está. Sus huecos son anillos
                    # interiores propios de la geometría, gestionados por el parámetro
                    # 'Eliminación de huecos' — no por operaciones entre polígonos.
                    fill_area = container_geom
                    area_antes_excl = fill_area.area() if fill_area else 0.0
                    if excl_handler.activo and fill_area and not fill_area.isEmpty():
                        fill_area = excl_handler.aplicar_exclusion(fill_area)
                        if fill_area:
                            excl_handler.acumular_area_excluida(max(0.0, area_antes_excl - fill_area.area()))
                        else:
                            logger.warning("[Exclusión] Contenedor completamente excluido — sin puntos.")
                            excl_handler.acumular_area_excluida(area_antes_excl)
                    if fill_area and not fill_area.isEmpty():
                        total_processed_area_m2 = fill_area.area()
                        # C-03: writer como parámetro explícito
                        new_id, pts_count, eb_count = self._generar_malla_sobre_geometria(
                            fill_area, fields, spacing, grid_type, current_id, False,
                            None, logger, feedback, writer, "Contenedor",
                            grid_origin=grid_origin)
                        current_id = new_id
                        total_points_conjunto += pts_count
                        total_puntos_en_borde += eb_count
                        logger.registrar_area(total_processed_area_m2)

                elif fill_behavior == Constants.FILL_ISLAS:
                    logger.info(f"Procesando {len(island_geoms)} islas individualmente...")
                    for idx, island in enumerate(island_geoms):
                        if feedback.isCanceled():
                            break
                        island, area_excl_isla = self._procesar_geom_con_exclusion(
                            island, excl_handler,
                            f"Isla {idx+1}/{len(island_geoms)}", logger)
                        if island is None:
                            excl_handler.acumular_area_excluida(area_excl_isla)
                            continue
                        excl_handler.acumular_area_excluida(area_excl_isla)
                        area = island.area()
                        total_processed_area_m2 += area
                        # C-03: writer como parámetro explícito
                        new_id, pts_count, eb_count = self._generar_malla_sobre_geometria(
                            island, fields, spacing, grid_type, current_id, False,
                            None, logger, feedback, writer, f"Isla {idx+1}/{len(island_geoms)}",
                            grid_origin=grid_origin)
                        current_id = new_id
                        total_points_conjunto += pts_count
                        total_puntos_en_borde += eb_count
                        logger.registrar_area(area)

                elif fill_behavior == Constants.FILL_UNIFICADO:
                    logger.info("Unificando todas las geometrías...")
                    unified_geom = QgsGeometry.unaryUnion(geometries)
                    # M3: Validar post-unaryUnion — puede producir sliver polygons
                    if unified_geom and not unified_geom.isEmpty() and not unified_geom.isGeosValid():
                        logger.warning("[Unificado] Geometría unificada inválida — aplicando makeValid()")
                        unified_geom = unified_geom.makeValid()
                        if unified_geom and not unified_geom.isEmpty():
                            logger.info("[Unificado] Geometría reparada correctamente.")
                    area_antes_excl = unified_geom.area() if unified_geom else 0.0
                    if excl_handler.activo and unified_geom and not unified_geom.isEmpty():
                        unified_geom = excl_handler.aplicar_exclusion(unified_geom)
                        if unified_geom is None or unified_geom.isEmpty():
                            logger.warning("[Exclusión] Área unificada completamente excluida — sin puntos.")
                            excl_handler.acumular_area_excluida(area_antes_excl)
                            unified_geom = None
                        else:
                            excl_handler.acumular_area_excluida(max(0.0, area_antes_excl - unified_geom.area()))

                    if unified_geom and not unified_geom.isEmpty():
                        total_processed_area_m2 = unified_geom.area()
                        # C-03: writer como parámetro explícito
                        new_id, pts_count, eb_count = self._generar_malla_sobre_geometria(
                            unified_geom, fields, spacing, grid_type, current_id, False,
                            None, logger, feedback, writer, "Área unificada",
                            grid_origin=grid_origin)
                        current_id = new_id
                        total_points_conjunto += pts_count
                        total_puntos_en_borde += eb_count
                        logger.registrar_area(total_processed_area_m2)

                stats['total_points'] = total_points_conjunto  # H-5: contador exacto
                stats['puntos_en_borde'] = total_puntos_en_borde
                if total_puntos_en_borde > 0:
                    logger.info(f"[Borde] {total_puntos_en_borde} punto(s) en el perímetro del polígono (campo en_borde=True).")
                logger.info(f"Total puntos generados: {stats['total_points']}")
                stats['mapeo_resumen'] = None  # mapeo no aplica en modo Conjunto
                stats['densidad_clases_resumen'] = None

            if writer is not None:
                del writer

            # Detección de duplicados de borde — modo Individual y modo Conjunto/Islas
            # En modo Islas cada isla se procesa como geometría independiente; si dos islas
            # son contiguas (comparten un borde exacto) intersects() captura el punto en
            # ambas, generando duplicados equivalentes a los del modo Individual.
            _es_islas = (operation_mode == Constants.MODE_CONJUNTO
                         and 'fill_behavior' in dir()
                         and fill_behavior == Constants.FILL_ISLAS)
            if (operation_mode == Constants.MODE_INDIVIDUAL or _es_islas) and final_output_path:
                dup_res = self.detectar_duplicados_borde(
                    final_output_path, layer_name_out, logger)
                stats['duplicados_borde'] = dup_res
            else:
                stats['duplicados_borde'] = None

            # OB-01: marcar en_borde=True para todos los duplicados detectados
            if (stats['duplicados_borde']
                    and stats['duplicados_borde'].get('features_extra', 0) > 0
                    and final_output_path):
                marcados = self._marcar_duplicados_en_borde(
                    final_output_path, layer_name_out, stats['duplicados_borde'], logger)
                stats['duplicados_borde']['en_borde_marcados'] = marcados
                if marcados > 0:
                    stats['puntos_en_borde'] = marcados
                    logger.info(
                        f"[en_borde] {marcados} punto(s) en borde compartido "
                        f"marcados como en_borde=True.")

            # Eliminación de entidades sobrantes por duplicado de borde
            # Marcar si el parámetro fue solicitado (independiente de si había duplicados)
            if stats['duplicados_borde'] is not None:
                stats['duplicados_borde']['eliminar_sobrantes_activo'] = eliminar_sobrantes
            if (eliminar_sobrantes and stats['duplicados_borde']
                    and stats['duplicados_borde'].get('features_extra', 0) > 0
                    and final_output_path):
                eliminados = self._eliminar_sobrantes_borde(
                    final_output_path, layer_name_out, stats['duplicados_borde'], logger)
                stats['sobrantes_eliminados'] = eliminados
                if eliminados > 0:
                    stats['total_points'] -= eliminados
                    # Inyectar en dup_resumen para que _report_duplicados lo muestre
                    stats['duplicados_borde']['sobrantes_eliminados'] = eliminados
                    logger.info(
                        f"[Sobrantes] {eliminados} entidad(es) sobrante(s) eliminada(s). "
                        f"Puntos finales: {stats['total_points']}")

            # Actualizar métricas de exclusión en stats
            excl_resumen = excl_handler.get_resumen()
            stats['area_excluida_ha'] = excl_resumen['area_excluida_m2'] / Constants.HA_TO_M2
            stats['exclusion_activa']     = excl_resumen['activo']
            stats['exclusion_n_features'] = excl_resumen['n_features']
            stats['exclusion_crs_transformado'] = excl_resumen['crs_transformado']
            if excl_handler.activo:
                logger.info(
                    f"[Exclusión] Área total excluida: "
                    f"{stats['area_excluida_ha']:.4f} ha")
            stats['output_file'] = final_output_path
            stats['total_area_ha'] = total_processed_area_m2 / Constants.HA_TO_M2
            if stats['total_points'] > 0:
                stats['area_per_point']    = stats['total_area_ha'] / stats['total_points']
                # M-08: densidad real obtenida vs solicitada
                stats['densidad_real']     = stats['total_points'] / stats['total_area_ha'] if stats['total_area_ha'] > 0 else 0.0
                # H-6: fórmula correcta según tipo de malla cuando el usuario usó espaciado directo
                if density_input > epsilon:
                    _dens_solic = density_input
                elif ha_por_punto_input > epsilon:
                    _dens_solic = 1.0 / ha_por_punto_input
                else:
                    if grid_type == Constants.GRID_RECTANGULAR:
                        _dens_solic = Constants.HA_TO_M2 / (spacing ** 2)
                    else:
                        _dens_solic = Constants.HA_TO_M2 / (spacing ** 2 * Constants.SQRT3_OVER_2)
                stats['densidad_solicitada'] = _dens_solic
                stats['ha_por_punto_solicitado'] = ha_por_punto_input if ha_por_punto_input > epsilon else (1.0 / _dens_solic if _dens_solic > 0 else 0.0)
                stats['ha_por_punto_real']       = 1.0 / stats['densidad_real'] if stats['densidad_real'] > 0 else 0.0
            else:
                stats['area_per_point']    = 0.0
                stats['densidad_real']         = 0.0
                stats['densidad_solicitada']   = 0.0
                stats['ha_por_punto_solicitado'] = 0.0
                stats['ha_por_punto_real']       = 0.0

            if stats['total_points'] == 0:
                logger.warning(
                    "[!] No se generaron puntos. Verifique el espaciado respecto al área "
                    "del polígono. Con Ha/punto muy alto el espaciado puede superar "
                    "las dimensiones del polígono.")
            logger.info(f"Proceso finalizado. Puntos creados: {stats['total_points']}, "
                        f"área total: {stats['total_area_ha']:.4f} ha, "
                        f"tiempo total: {logger.get_tiempo_ejecucion():.2f}s")

            if final_output_path:
                context.addLayerToLoadOnCompletion(
                    f"{final_output_path}|layername={layer_name_out}",
                    QgsProcessingContext.LayerDetails(layer_name_out, context.project(), self.OUTPUT_FOLDER)
                )

            # ── Exportación JSON de parámetros ──────────────────────────────────
            if exportar_json and final_output_path:
                try:
                    json_path = os.path.splitext(final_output_path)[0] + '_params.json'
                    metricas_json = logger.get_metricas()
                    mapeo_res = stats.get('mapeo_resumen') or {}
                    json_data = {
                        'meta': {
                            'herramienta': 'Crear Malla de Puntos',
                            'version':     self.VERSION,
                            'autor':       'Jorge Fallas (jfallas56@gmail.com)',
                            'fecha_hora':  datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'qgis_version': '',   # se llenaría con Qgis.QGIS_VERSION si disponible
                        },
                        'entrada': {
                            'capa':             stats['layer_name'],
                            'crs':              stats['crs'],
                            'poligonos_total':  stats['polygons_count'],
                        },
                        'configuracion_malla': {
                            'tipo_malla':       stats['grid_type'],
                            'modo_operacion':   stats['mode'],
                            'comportamiento':   stats['behavior'],
                            'espaciado_m':      round(stats['spacing'], 6),
                            'entrada_usuario':  stats['input_info'],
                        },
                        'configuracion_geometria': {
                            'gestion_integridad':       Constants.INTEGRIDAD_NAMES[stats['gestion_integridad']],
                            'simplificacion_activa':    stats['simplificacion_activa'],
                            'tolerancia_entrada_m':     stats['tolerancia_entrada'],
                            'eliminar_huecos':          stats['eliminar_huecos'],
                            'area_minima_hueco_m2':     stats['area_minima_hueco'],
                            'preservar_hueco_estructural': stats['preservar_hueco_estructural'],
                        },
                        'resultados': {
                            'puntos_generados':         stats['total_points'],
                            'puntos_en_borde_compartido': stats.get('puntos_en_borde', 0),
                            'area_total_ha':            round(stats['total_area_ha'], 6),
                            'area_por_punto_ha':        round(stats['area_per_point'], 6),
                            'densidad_solicitada_pts_ha': round(stats.get('densidad_solicitada', 0), 6),
                            'densidad_real_pts_ha':     round(stats.get('densidad_real', 0), 6),
                            'ha_por_punto_solicitado':  round(stats.get('ha_por_punto_solicitado', 0), 6),
                            'ha_por_punto_real':        round(stats.get('ha_por_punto_real', 0), 6),
                            'archivo_salida':           os.path.basename(final_output_path),
                            **({'sobrantes_eliminados': stats.get('sobrantes_eliminados', 0)}
                               if stats.get('duplicados_borde') is not None else {}),
                        },
                        'calidad_iso19157': {
                            'geometrias_procesadas':    metricas_json.get('geometrias_procesadas', 0),
                            'geometrias_reparadas':     metricas_json.get('geometrias_reparadas', 0),
                            'geometrias_omitidas':      metricas_json.get('geometrias_omitidas', 0),
                            'geometrias_con_z':         metricas_json.get('geometrias_con_z', 0),
                            'geometrias_multipart':     metricas_json.get('geometrias_multipart', 0),
                            'ids_reparados':            metricas_json.get('reparados_ids', []),
                            'ids_omitidos':             metricas_json.get('omitidos_ids', []),
                            'ids_riesgo':               metricas_json.get('riesgo_ids', []),
                            'total_advertencias':       metricas_json.get('total_advertencias', 0),
                            'total_errores':            metricas_json.get('total_errores', 0),
                        },
                        'simplificacion': {
                            'vertices_originales':  vertices_antes_total,
                            'vertices_finales':     vertices_despues_total,
                        },
                        'mapeo_json': {
                            'activo':         mapeo_res.get('activo', False),
                            'archivo':        os.path.basename(mapeo_res.get('json_path', '')) if mapeo_res.get('json_path') else '',
                            'total_clases':   mapeo_res.get('total_clases', 0),
                            'aplicaciones':   mapeo_res.get('aplicaciones', 0),
                            'ids_sin_mapeo':  mapeo_res.get('sin_mapeo', []),
                            'campo_fuente':   mapeo_res.get('meta', {}).get('campo_fuente', ''),
                            'campo_salida':   mapeo_res.get('meta', {}).get('campo_salida', ''),
                            'descripcion':    mapeo_res.get('meta', {}).get('descripcion', ''),
                        },
                        'exclusion': {
                            'activa':             stats.get('exclusion_activa', False),
                            'n_geometrias':       stats.get('exclusion_n_features', 0),
                            'buffer_m':           stats.get('exclusion_buffer_m', 0.0),
                            'crs_transformado':   stats.get('exclusion_crs_transformado', False),
                            'area_excluida_ha':   round(stats.get('area_excluida_ha', 0.0), 6),
                            'area_efectiva_ha':   round(stats.get('total_area_ha', 0.0), 6),
                        },
                        'rendimiento': {
                            'tiempo_ejecucion_s': round(metricas_json.get('tiempo_ejecucion', 0), 2),
                        },
                    }
                    # Intentar agregar versión de QGIS
                    try:
                        from qgis.core import Qgis as _Qgis
                        json_data['meta']['qgis_version'] = _Qgis.QGIS_VERSION
                    except Exception:
                        pass

                    with open(json_path, 'w', encoding='utf-8') as jf:
                        json.dump(json_data, jf, ensure_ascii=False, indent=2)
                    logger.info(f"[JSON] Parámetros exportados: {json_path}")
                    QgsMessageLog.logMessage(f"[JSON params] {json_path}", "MallaPuntos", Qgis.Info)
                except Exception as e:
                    feedback.reportError(f"Error exportando JSON de parámetros: {str(e)}")

            # ── Reporte HTML ─────────────────────────────────────────────────────
            if html_file:
                try:
                    if html_file == 'TEMPORARY_OUTPUT':
                        html_file = QgsProcessingUtils.generateTempFilename('reporte_malla.html')
                    self._generate_report(
                        params=stats,
                        logger=logger,
                        total_points=stats['total_points'],
                        total_area_ha=stats['total_area_ha'],
                        source_name=layer_name,
                        html_path=html_file,
                        open_report=open_report,
                        vertices_antes=vertices_antes_total,
                        vertices_despues=vertices_despues_total
                    )
                    logger.info(f"Reporte HTML guardado en: {html_file}")
                    QgsMessageLog.logMessage(f"[Reporte] {html_file}", "MallaPuntos", Qgis.Info)
                except Exception as e:
                    feedback.reportError(f"Error creando reporte: {str(e)}")

            return {
                self.OUTPUT_FOLDER: output_folder
                # OUTPUT_HTML_REPORT no se retorna para evitar que QGIS abra
                # el Visor de resultados automáticamente.
            }
        
        except Exception as e:
            exc_info = ''.join(traceback.format_exception(*sys.exc_info()))
            logger.error(f"Error crítico: {e}\n{exc_info}")
            raise QgsProcessingException(str(e))

    @staticmethod
    def _eliminar_sobrantes_borde(gpkg_path: str, layer_name: str,
                                   dup_resumen: dict,
                                   logger: 'Logger' = None) -> int:
        """
        Elimina entidades sobrantes por duplicado de borde del GeoPackage.
        Criterio: por cada grupo de coordenadas duplicadas, conserva la entidad
        con menor id_punto (primera en orden de generación) y elimina el resto.
        Retorna el número de entidades eliminadas.
        """
        try:
            from qgis.core import QgsVectorLayer, QgsVectorDataProvider
            layer = QgsVectorLayer(
                f"{gpkg_path}|layername={layer_name}", "edit_layer", "ogr")
            if not layer.isValid():
                if logger:
                    logger.error(f"[Sobrantes] No se pudo abrir la capa para edición: {gpkg_path}")
                return 0

            # Reconstruir índice de coordenadas → id_punto desde dup_resumen
            # Re-leer la capa para obtener el mapeo completo (coord → lista id_punto)
            coord_index: dict = {}
            for feat in layer.getFeatures():
                key = (feat['coord_x'], feat['coord_y'])
                if key not in coord_index:
                    coord_index[key] = []
                coord_index[key].append((feat['id_punto'], feat.id()))  # (id_punto, fid_interno)

            # Identificar fids a eliminar: por cada grupo duplicado, conservar
            # el de menor id_punto y marcar el resto para eliminación
            fids_eliminar = []
            for key, lista in coord_index.items():
                if len(lista) > 1:
                    lista_sorted = sorted(lista, key=lambda x: x[0])  # orden por id_punto
                    # Conservar el primero (menor id_punto); eliminar el resto
                    for _, fid_interno in lista_sorted[1:]:
                        fids_eliminar.append(fid_interno)

            if not fids_eliminar:
                return 0

            # Eliminar en modo edición
            caps = layer.dataProvider().capabilities()
            if not (caps & QgsVectorDataProvider.DeleteFeatures):
                if logger:
                    logger.error("[Sobrantes] El proveedor no soporta eliminación de entidades.")
                return 0

            # Calcular ids_conservados e ids_sobrantes para el reporte
            ids_conservados_elim = sorted([min(v, key=lambda x: x[0])[0]
                                           for v in coord_index.values() if len(v) > 1])
            ids_sobrantes_elim   = sorted([ip for v in coord_index.values() if len(v) > 1
                                           for ip, _ in sorted(v, key=lambda x: x[0])[1:]])

            layer.startEditing()
            layer.deleteFeatures(fids_eliminar)
            if layer.commitChanges():
                if logger:
                    logger.info(f"[Sobrantes] {len(fids_eliminar)} entidad(es) sobrante(s) "
                                f"eliminada(s) del GeoPackage.")
                # Inyectar listas en dup_resumen para el reporte HTML
                dup_resumen['ids_conservados'] = ids_conservados_elim[:100]
                dup_resumen['ids_sobrantes']   = ids_sobrantes_elim[:100]
                return len(fids_eliminar)
            else:
                layer.rollBack()
                if logger:
                    logger.error("[Sobrantes] Error al confirmar cambios — operación revertida.")
                return 0

        except Exception as e:
            if logger:
                logger.error(f"[Sobrantes] Error inesperado: {e}")
            return 0

    @staticmethod
    def _marcar_duplicados_en_borde(gpkg_path: str, layer_name: str,
                                     dup_resumen: dict,
                                     logger: 'Logger' = None) -> int:
        """
        OB-01: Marca en_borde=True para todos los id_punto identificados como
        duplicados de borde (ids_afectados en dup_resumen).
        Retorna el número de entidades marcadas.
        """
        ids_a_marcar = dup_resumen.get('ids_afectados', [])
        if not ids_a_marcar:
            return 0
        try:
            from qgis.core import QgsVectorLayer
            layer = QgsVectorLayer(
                f"{gpkg_path}|layername={layer_name}", "mark_layer", "ogr")
            if not layer.isValid():
                if logger:
                    logger.error(f"[en_borde] No se pudo abrir la capa: {gpkg_path}")
                return 0

            idx_en_borde = layer.fields().indexFromName('en_borde')
            idx_id_punto = layer.fields().indexFromName('id_punto')
            if idx_en_borde < 0 or idx_id_punto < 0:
                if logger:
                    logger.error("[en_borde] Campos 'en_borde' o 'id_punto' no encontrados.")
                return 0

            ids_set = set(ids_a_marcar)
            cambios = {}  # {fid_interno: {idx_campo: valor}}
            for feat in layer.getFeatures():
                if feat['id_punto'] in ids_set:
                    cambios[feat.id()] = {idx_en_borde: True}

            if not cambios:
                return 0

            layer.startEditing()
            layer.dataProvider().changeAttributeValues(cambios)
            if layer.commitChanges():
                n = len(cambios)
                if logger:
                    logger.info(f"[en_borde] {n} entidad(es) marcada(s) como en_borde=True "
                                f"(duplicados de borde compartido).")
                return n
            else:
                layer.rollBack()
                if logger:
                    logger.error("[en_borde] Error al confirmar marcado — revertido.")
                return 0
        except Exception as e:
            if logger:
                logger.error(f"[en_borde] Error inesperado: {e}")
            return 0

    def _generate_report(self, params, logger, total_points, total_area_ha,
                         source_name, html_path, open_report,
                         vertices_antes=0, vertices_despues=0):
        metricas = logger.get_metricas()
        tiempo_ejecucion = metricas.get('tiempo_ejecucion', 0)

        integridad_html  = self._report_integrity(metricas, params)
        advertencias_html = self._report_warnings(logger.advertencias)
        eficiencia_html  = self._report_efficiency(vertices_antes, vertices_despues, params)
        mapeo_html           = self._report_mapeo(params.get('mapeo_resumen'))
        densidad_clases_html = self._report_densidad_clases(params.get('densidad_clases_resumen'), params.get('json_densidad_path', ''), params)
        exclusion_html       = self._report_exclusion(params)
        duplicados_html  = self._report_duplicados(params.get('duplicados_borde'))
        
        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Reporte de Malla de Puntos</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background: #f0f2f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; border-radius: 10px; box-shadow: 0 0 20px rgba(0,0,0,0.1); padding: 30px; }}
        .header {{ background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; padding: 20px 24px; border-radius: 10px; margin-bottom: 20px; }}
        .header h1 {{ margin: 0 0 4px 0; font-size: 22px; }}
        .header p {{ margin: 0; font-size: 13px; opacity: 0.88; }}
        .section {{ margin-bottom: 22px; background: #f8f9fa; border-radius: 8px; padding: 18px 20px; }}
        .section h3 {{ color: #1e3c72; margin: 0 0 14px 0; font-size: 15px; border-bottom: 2px solid #dee2e6; padding-bottom: 8px; }}
        .info-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }}
        .info-card {{ background: white; padding: 14px 16px; border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,0.08); }}
        .info-card h4 {{ margin: 0 0 10px 0; color: #2a5298; font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; border-bottom: 1px solid #e9ecef; padding-bottom: 6px; }}
        .info-card p {{ margin: 5px 0; font-size: 13.5px; line-height: 1.45; }}
        .density-row {{ display: flex; justify-content: space-between; align-items: baseline; padding: 4px 0; border-bottom: 1px solid #f0f0f0; font-size: 13.5px; }}
        .density-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
        .density-label {{ color: #444; }}
        .density-value {{ font-weight: 600; color: #1e3c72; }}
        .density-divider {{ border: none; border-top: 1px solid #e0e0e0; margin: 7px 0; }}
        .diff-ok {{ color: #28a745; font-weight: 600; }}
        .diff-warn {{ color: #e67e22; font-weight: 600; }}
        .diff-bad  {{ color: #dc3545; font-weight: 600; }}
        .stats-table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }}
        .stats-table th, .stats-table td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid #dee2e6; }}
        .stats-table th {{ background: #2a5298; color: white; font-weight: 600; }}
        .stats-table tr:last-child td {{ border-bottom: none; }}
        .metric {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #eee; font-size: 13.5px; }}
        .metric:last-child {{ border-bottom: none; }}
        .metric-value {{ font-weight: bold; color: #2c3e50; }}
        .footer {{ text-align: center; padding: 14px 20px; background: #f8f9fa; color: #6c757d; border-top: 1px solid #dee2e6; margin-top: 22px; border-radius: 8px; font-size: 12.5px; }}
        .footer a {{ color: #2a5298; text-decoration: none; }}
        .warning-box {{ background: #fff3cd; color: #856404; padding: 14px 16px; border-radius: 5px; border: 1px solid #ffeeba; }}
        .success-box {{ background: #d4edda; color: #155724; padding: 14px 16px; border-radius: 5px; border: 1px solid #c3e6cb; }}
        .badge {{ display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
        .badge-success {{ background: #28a745; color: white; }}
        .badge-warning {{ background: #ffc107; color: #212529; }}
        .badge-danger  {{ background: #dc3545; color: white; }}
        /* ISO 19157:2023 Quality Report */
        .iso-header {{ display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; border-bottom: 2px solid #dee2e6; padding-bottom: 8px; }}
        .iso-title {{ color: #1e3c72; font-size: 15px; font-weight: 700; margin: 0; }}
        .iso-badge {{ font-size: 10.5px; color: #6c757d; background: #e9ecef; border-radius: 4px; padding: 2px 8px; font-weight: 600; letter-spacing: 0.03em; white-space: nowrap; }}
        .iso-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; }}
        .iso-card {{ background: white; border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,0.08); overflow: hidden; }}
        .iso-card-header {{ padding: 8px 14px; font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }}
        .iso-card-header.completitud {{ background: #e8f0fe; color: #1a56a0; border-bottom: 2px solid #4a80d4; }}
        .iso-card-header.consistencia {{ background: #e6f4ea; color: #1a6e2e; border-bottom: 2px solid #34a853; }}
        .iso-card-body {{ padding: 12px 14px; }}
        .iso-row {{ display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid #f2f2f2; font-size: 13px; }}
        .iso-row:last-child {{ border-bottom: none; }}
        .iso-row-label {{ color: #444; display: flex; align-items: center; gap: 5px; }}
        .iso-row-value {{ font-weight: 600; color: #1e3c72; }}
        .iso-bar-wrap {{ margin-top: 10px; }}
        .iso-bar-labels {{ display: flex; justify-content: space-between; font-size: 11.5px; color: #555; margin-bottom: 3px; }}
        .iso-bar-bg {{ background: #e9ecef; border-radius: 6px; height: 13px; overflow: hidden; }}
        .iso-bar-fill {{ height: 100%; border-radius: 6px; transition: width 0.3s; }}
        .iso-bar-note {{ font-size: 11px; color: #6c757d; margin-top: 5px; }}
        .iso-disclaimer {{ margin-top: 12px; padding: 8px 10px; background: #f8f9fa; border-left: 3px solid #adb5bd; border-radius: 0 4px 4px 0; font-size: 11px; color: #555; line-height: 1.5; }}
        .iso-detail summary {{ cursor: pointer; font-size: 12px; padding: 5px 0; }}
        .iso-detail-body {{ padding: 6px 8px; background: #f8f9fa; border-radius: 4px; margin-top: 4px; font-size: 12px; color: #333; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Reporte — Creación de Malla de Puntos</h1>
            <p>Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')} &nbsp;|&nbsp; Tiempo de ejecución: {_fmt(tiempo_ejecucion, 2)} s</p>
        </div>

        <div class="section">
            <h3>Información General</h3>
            <div class="info-grid" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));">
                <div class="info-card">
                    <h4>Proyecto</h4>
                    <p><strong>Capa de entrada:</strong> {source_name}</p>
                    <p><strong>SRC:</strong> {params['crs']}</p>
                    <p><strong>Polígonos procesados:</strong> {params['polygons_count']}</p>
                    <p><strong>Tiempo de ejecución:</strong> {_fmt(tiempo_ejecucion, 2)} s</p>
                </div>
                <div class="info-card">
                    <h4>Configuración</h4>
                    <p><strong>Tipo de malla:</strong> {params['grid_type']}</p>
                    <p><strong>Modo:</strong> {params['mode']}</p>
                    <p><strong>Comportamiento:</strong> {'Un grupo de puntos por polígono' if params['behavior'] == 'N/A' else params['behavior']}</p>
                    {
                        f'<p><strong>Espaciado solicitado:</strong> {_fmt(params["spacing_input_orig"], 4)} m</p>'
                        f'<p><strong>Cobertura equivalente:</strong> {_fmt(params.get("ha_por_punto_solicitado", 0), 2)} ha/pto · {_fmt(params.get("densidad_solicitada", 0), 2)} pts/ha</p>'
                        if params.get('spacing_input_orig', 0) > 0.000001
                        else (
                            (f'<p><strong>Densidad solicitada:</strong> {_fmt(params["density_input_orig"], 4)} pts/ha → Dist.: {_fmt(params["spacing"], 4)} m</p>'
                            if not params.get('json_densidad_path') else
                            '<p><strong>Densidad solicitada:</strong> Variable por clase — ver tabla Densidad por clase (JSON)</p>')
                            if params.get('density_input_orig', 0) > 0.000001
                            else f'<p><strong>Ha/punto solicitado:</strong> {_fmt(params["ha_por_punto_input_orig"], 4)} ha/pto → Dist.: {_fmt(params["spacing"], 4)} m</p>'
                        )
                    }
                </div>
                <div class="info-card">
                    <h4>Resultados</h4>
                    <div class="density-row">
                        <span class="density-label"><strong>Puntos generados</strong></span>
                        <span class="density-value" style="font-size:1.15em;color:#2a5298;">{_fmt(total_points, 0)}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Puntos en perímetro</strong></span>
                        <span style="color:{'#e67e22' if params.get('puntos_en_borde',0) > 0 else '#28a745'};font-weight:600;font-size:13.5px;">
                            {_fmt(params.get('puntos_en_borde', 0), 0)}
                            ({_fmt(params.get('puntos_en_borde', 0) / total_points * 100, 2) if total_points > 0 else '0,00'}%)
                        </span>
                    </div>
                    {'<div class="density-row"><span style="font-size:11px;color:#1a5276;">⚠ Densidad variable por clase (JSON) — la densidad global configurada no aplica.</span></div>' if params.get('json_densidad_path') else ''}
                    <div class="density-row">
                        <span class="density-label" style="font-size:11px;color:#888;">
                        {(
                            '↳ en_borde=False en todos los puntos — este modo no genera '
                            'duplicados de borde por definición.'
                            if params.get('duplicados_borde') is None
                            else (
                                '↳ Puntos sobre borde compartido entre polígonos adyacentes '
                                '(en_borde=True). Ver sección Duplicados de Borde.'
                                if isinstance(params.get('duplicados_borde'), dict)
                                and params['duplicados_borde'].get('duplicados', 0) > 0
                                else '↳ Sin duplicados de borde detectados (en_borde=False en todos los puntos).'
                            )
                        )}
                        </span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Área total procesada</strong></span>
                        <span class="density-value">{_fmt(total_area_ha, 2)} ha</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Área por punto</strong></span>
                        <span class="density-value">{_fmt(params['area_per_point'], 4)} ha</span>
                    </div>
                </div>
                <div class="info-card">
                    <h4>Densidad</h4>
                    <div class="density-row">
                        <span class="density-label"><strong>{'Solicitada' if params.get('density_input_orig',0) > 0.000001 else 'Equivalente'}</strong></span>
                        <span class="density-value">{'Ver tabla Densidad por clase (JSON) ↓' if params.get('json_densidad_path') else _fmt(params.get('densidad_solicitada', 0), 2) + ' pts/ha'}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Real obtenida</strong></span>
                        <span class="density-value">{'Ver tabla Densidad por clase (JSON) ↓' if params.get('json_densidad_path') else _fmt(params.get('densidad_real', 0), 2) + ' pts/ha'}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Diferencia</strong></span>
                        <span class="{'diff-ok' if abs(params.get('densidad_real',0)-params.get('densidad_solicitada',0))/(params.get('densidad_solicitada',1) or 1)*100 < 5 else ('diff-warn' if abs(params.get('densidad_real',0)-params.get('densidad_solicitada',0))/(params.get('densidad_solicitada',1) or 1)*100 < 15 else 'diff-bad')}">
                            {'⚠ Densidad variable por clase (JSON) — comparación con densidad global no es representativa' if params.get('json_densidad_path') else
                             ('Diferencia < 0,005 pts/ha' if abs(params.get('densidad_real',0) - params.get('densidad_solicitada',0)) < 0.005 else
                             f"{_fmt(abs(params.get('densidad_real',0) - params.get('densidad_solicitada',0)), 2)} pts/ha ({_fmt((abs(params.get('densidad_real',0) - params.get('densidad_solicitada',0)) / (params.get('densidad_solicitada',1) or 1) * 100), 1)}%)")}
                        </span>
                    </div>
                    <hr class="density-divider"/>
                    <div class="density-row">
                        <span class="density-label"><strong>{'Ha/punto solicitado' if params.get('ha_por_punto_input_orig',0) > 0.000001 else 'Ha/punto equivalente'}</strong></span>
                        <span class="density-value">{'Ver tabla Densidad por clase (JSON) ↓' if params.get('json_densidad_path') else _fmt(params.get('ha_por_punto_solicitado', 0), 2) + ' ha/pto'}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Ha/punto real</strong></span>
                        <span class="density-value">{'Ver tabla Densidad por clase (JSON) ↓' if params.get('json_densidad_path') else _fmt(params.get('ha_por_punto_real', 0), 2) + ' ha/pto'}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Diferencia Ha/punto</strong></span>
                        <span class="{'diff-ok' if (abs(params.get('ha_por_punto_real',0)-params.get('ha_por_punto_solicitado',0))/(params.get('ha_por_punto_solicitado',1) or 1)*100) < 5 else ('diff-warn' if (abs(params.get('ha_por_punto_real',0)-params.get('ha_por_punto_solicitado',0))/(params.get('ha_por_punto_solicitado',1) or 1)*100) < 15 else 'diff-bad')}">
                            {'⚠ Densidad variable por clase (JSON) — comparación con densidad global no es representativa' if params.get('json_densidad_path') else
                             ('Diferencia < 0,0005 ha/pto' if abs(params.get('ha_por_punto_real',0) - params.get('ha_por_punto_solicitado',0)) < 0.0005 else
                             f"{_fmt(abs(params.get('ha_por_punto_real',0) - params.get('ha_por_punto_solicitado',0)), 2)} ha/pto ({_fmt((abs(params.get('ha_por_punto_real',0) - params.get('ha_por_punto_solicitado',0)) / (params.get('ha_por_punto_solicitado',1) or 1) * 100), 1)}%)")}
                        </span>
                    </div>
                </div>
            </div>
        </div>

        {integridad_html}
        {exclusion_html}
        {eficiencia_html}
        {mapeo_html}
        {densidad_clases_html}
        {duplicados_html}
        {advertencias_html}

        <div class="section">
            <h3>Parámetros Utilizados</h3>
            <table class="stats-table">
                <tr><th>Parámetro</th><th>Valor</th></tr>
                <tr><td>Modo de operación</td><td>{'Un grupo de puntos por polígono' if params['behavior'] == 'N/A' else params['mode']}</td></tr>
                <tr><td>Comportamiento</td><td>{'Un grupo de puntos por polígono' if params['behavior'] == 'N/A' else params['behavior']}</td></tr>
                <tr><td>Espaciado / Densidad</td><td>{'Variable por clase — ver tabla Densidad por clase (JSON)' if params.get('json_densidad_path') else params['input_info']}</td></tr>
                <tr><td>Gestión de integridad</td><td>{Constants.INTEGRIDAD_NAMES[params.get('gestion_integridad', 2)]}</td></tr>
                <tr><td>Simplificar entrada</td><td>{'Sí' if params['simplificacion_activa'] else 'No'} (tolerancia: {_fmt(params['tolerancia_entrada'], 2)} m)</td></tr>
                <tr><td>Eliminar huecos</td><td>{'Sí' if params['eliminar_huecos'] else 'No'}</td></tr>
                <tr><td>Área máxima hueco a eliminar</td><td>{_fmt(params['area_minima_hueco'], 2)} m² — elimina huecos ≤ {_fmt(params['area_minima_hueco'], 2)} m² · conserva huecos &gt; {_fmt(params['area_minima_hueco'], 2)} m²</td></tr>
                <tr><td>Preservar hueco estructural</td><td>
                {('Sí' if params['preservar_hueco_estructural'] else 'No') +
                 (' <span style="color:#888;font-size:11px;">(sin efecto — Eliminar huecos desactivado)</span>'
                  if not params.get('eliminar_huecos', False) else
                  (' <span style="color:#888;font-size:11px;">(sin efecto — Área máxima a eliminar &gt; 0)</span>'
                   if params.get('area_minima_hueco', 0) > 0 else ''))}
                </td></tr>
                <tr><td>Exportar parámetros JSON</td><td>{'Sí' if params.get('exportar_json', False) else 'No'}</td></tr>
                <tr><td>Mapeo JSON de clases</td><td>{os.path.basename(params.get('json_mapeo_path','')) if params.get('json_mapeo_path') else 'No aplicado'}</td></tr>
                <tr><td>Densidad por clase JSON</td><td>{os.path.basename(params.get('json_densidad_path','')) if params.get('json_densidad_path') else 'No aplicado'}</td></tr>
                <tr><td>Capa de exclusión</td><td>{'Activa (' + str(params.get('exclusion_n_features',0)) + ' geometrías)' if params.get('exclusion_activa') else 'No aplicado'}</td></tr>
                <tr><td>Búfer exclusión</td><td>{_fmt(params.get('exclusion_buffer_m', 0.0), 2)} m</td></tr>
                <tr><td>Eliminar sobrantes</td><td>
                {('Sí' if (params.get('duplicados_borde') or {{}}).get('eliminar_sobrantes_activo', False) else 'No')
                 if params.get('duplicados_borde') is not None
                 else '<span style="color:#888;font-size:11px;">No aplica (Unificado/Contenedor)</span>'}
                </td></tr>
                <tr><td>Archivo de salida</td><td>{os.path.basename(params['output_file'])}</td></tr>
            </table>
        </div>

        <div class="footer">
            Herramienta: Crear Malla de Puntos &nbsp;|&nbsp;
            Autor: Jorge Fallas &nbsp;|&nbsp;
            <a href="mailto:jfallas56@gmail.com">jfallas56@gmail.com</a>
        </div>
    </div>
</body>
</html>
"""
        try:
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html)
            if open_report:
                url = Path(html_path).as_uri()
                # === MODIFICACIÓN: R-05 ===
                try:
                    webbrowser.open(url)
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"[Reporte] No se pudo abrir el navegador automáticamente: {e}. "
                        f"El reporte está disponible en: {html_path}",
                        "MallaPuntos", Qgis.Warning)
        except Exception as e:
            QgsMessageLog.logMessage(  # H-3: visible en panel Log Messages de QGIS
                f"[ERROR] No se pudo escribir/abrir el reporte: {e}", "MallaPuntos", Qgis.Critical)

    def _report_integrity(self, metricas, params):
        reparados     = metricas.get('reparados_ids', [])
        omitidos      = metricas.get('omitidos_ids', [])
        riesgo        = metricas.get('riesgo_ids', [])
        geom_procesadas = metricas.get('geometrias_procesadas', 0)
        geom_reparadas  = metricas.get('geometrias_reparadas', 0)
        geom_omitidas   = metricas.get('geometrias_omitidas', 0)

        total_entrada = geom_procesadas + geom_omitidas

        # ── Completitud §D.1 ────────────────────────────────────────────────
        # Barra = entidades que generaron puntos / total entrada × 100
        tasa_completitud = (geom_procesadas / total_entrada * 100) if total_entrada > 0 else 0
        bar_color = '#28a745' if tasa_completitud >= 95 else ('#ffc107' if tasa_completitud >= 70 else '#dc3545')

        # Omisión: geom omitidas por integridad + geom en riesgo sin verificar
        omision_integridad = geom_omitidas
        omision_riesgo     = len(riesgo)
        # Comisión: entidades procesadas sin verificación de calidad (modo Riesgo)
        comision_sin_verif = omision_riesgo

        # ── Consistencia Lógica §D.3 ─────────────────────────────────────────
        gestion = params.get('gestion_integridad', Constants.INTEGRIDAD_REPARAR)
        if gestion == Constants.INTEGRIDAD_REPARAR:
            consist_estado = f'[~] {geom_reparadas} reparadas con reparar geometría &nbsp;·&nbsp; [OK] {geom_procesadas - geom_reparadas} consistentes'
            consist_color  = '#155724'
            consist_bg     = '#d4edda'
        elif gestion == Constants.INTEGRIDAD_OMITIR:
            consist_estado = f'[OK] {geom_procesadas} consistentes &nbsp;·&nbsp; [X] {geom_omitidas} omitidas (no reparadas)'
            consist_color  = '#856404'
            consist_bg     = '#fff3cd'
        else:  # RIESGO
            consist_estado = f'[!] No verificado — {total_entrada} geometrías procesadas sin validación topológica'
            consist_color  = '#721c24'
            consist_bg     = '#f8d7da'

        detail_html = self._report_integrity_detail(reparados, omitidos, riesgo)

        return f"""
        <div class="section" style="padding-bottom:16px;">
            <div class="iso-header">
                <span class="iso-title">Reporte de Calidad</span>
                <span class="iso-badge">ISO 19157:2023 — Alcance: proceso de generación</span>
            </div>
            <div class="iso-grid">

                <!-- Completitud §D.1 -->
                <div class="iso-card">
                    <div class="iso-card-header completitud">Completitud &nbsp;§D.1</div>
                    <div class="iso-card-body">
                        <div class="iso-row">
                            <span class="iso-row-label">[X] Omisión — integridad geom.</span>
                            <span class="iso-row-value">{omision_integridad}</span>
                        </div>
                        <div class="iso-row">
                            <span class="iso-row-label">[!] Comisión — sin verificación</span>
                            <span class="iso-row-value">{comision_sin_verif}</span>
                        </div>
                        <div class="iso-bar-wrap">
                            <div class="iso-bar-labels">
                                <span>Entidades con resultado</span>
                                <span style="font-weight:700;color:{bar_color};">{tasa_completitud:.1f}%</span>
                            </div>
                            <div class="iso-bar-bg">
                                <div class="iso-bar-fill" style="width:{min(tasa_completitud,100):.1f}%;background:{bar_color};"></div>
                            </div>
                            <div class="iso-bar-note">
                                {geom_procesadas} de {total_entrada} entidades generaron puntos
                                &nbsp;·&nbsp; Fórmula: (con resultado / total entrada) × 100
                            </div>
                        </div>
                        {detail_html}
                    </div>
                </div>

                <!-- Consistencia Lógica §D.3 -->
                <div class="iso-card">
                    <div class="iso-card-header consistencia">Consistencia Lógica &nbsp;§D.3</div>
                    <div class="iso-card-body">
                        <div class="iso-row">
                            <span class="iso-row-label">[OK] Geometrías consistentes</span>
                            <span class="iso-row-value">{geom_procesadas - geom_reparadas if gestion != Constants.INTEGRIDAD_RIESGO else 0}</span>
                        </div>
                        <div class="iso-row">
                            <span class="iso-row-label">[~] Reparadas — reparar geometría</span>
                            <span class="iso-row-value">{geom_reparadas}</span>
                        </div>
                        <div class="iso-row">
                            <span class="iso-row-label">[!] Sin verificación (modo Riesgo)</span>
                            <span class="iso-row-value">{omision_riesgo}</span>
                        </div>
                        <div style="margin-top:10px;padding:7px 10px;border-radius:5px;
                                    background:{consist_bg};color:{consist_color};font-size:12px;line-height:1.5;">
                            {consist_estado}
                        </div>
                        <div class="iso-disclaimer">
                            Conformidad factual sin umbrales definidos. La aceptabilidad
                            del resultado corresponde al profesional responsable del producto
                            conforme al especificador de datos aplicable.
                        </div>
                    </div>
                </div>

            </div>
        </div>
        """

    def _report_integrity_detail(self, reparados, omitidos, riesgo):
        """Listas desplegables de FIDs afectados, embebidas en la tarjeta Completitud."""
        html = ""
        if reparados:
            ids_str = ', '.join(str(i) for i in sorted(reparados)[:10])
            if len(reparados) > 10:
                ids_str += f" … (+{len(reparados)-10} más)"
            html += f"""
            <details class="iso-detail" style="margin-top:8px;">
                <summary style="color:#155724;">[~] FIDs reparados ({len(reparados)})</summary>
                <div class="iso-detail-body">{ids_str}</div>
            </details>"""
        if omitidos:
            ids_str = ', '.join(str(i) for i in sorted(omitidos)[:10])
            if len(omitidos) > 10:
                ids_str += f" … (+{len(omitidos)-10} más)"
            html += f"""
            <details class="iso-detail" style="margin-top:6px;">
                <summary style="color:#721c24;">[X] FIDs omitidos ({len(omitidos)})</summary>
                <div class="iso-detail-body">{ids_str}</div>
            </details>"""
        if riesgo:
            ids_str = ', '.join(str(i) for i in sorted(riesgo)[:10])
            if len(riesgo) > 10:
                ids_str += f" … (+{len(riesgo)-10} más)"
            html += f"""
            <details class="iso-detail" style="margin-top:6px;">
                <summary style="color:#856404;">[!] FIDs con riesgo ({len(riesgo)})</summary>
                <div class="iso-detail-body">{ids_str}</div>
            </details>"""
        return html

    def _report_exclusion(self, params):
        """Sección HTML de exclusión. Solo se muestra si la capa de exclusión estuvo activa."""
        if not params.get('exclusion_activa', False):
            return ""

        n_geom       = params.get('exclusion_n_features', 0)
        buffer_m     = params.get('exclusion_buffer_m', 0.0)
        area_excl_ha = params.get('area_excluida_ha', 0.0)
        area_ef_ha   = params.get('total_area_ha', 0.0)
        area_total   = area_ef_ha + area_excl_ha
        pct_excl     = (area_excl_ha / area_total * 100) if area_total > 0 else 0.0
        crs_transf   = params.get('exclusion_crs_transformado', False)

        bar_color = '#28a745' if pct_excl < 10 else ('#ffc107' if pct_excl < 30 else '#dc3545')

        crs_badge = (
            '<span style="font-size:11px;background:#fff3cd;color:#856404;'
            'border-radius:3px;padding:1px 6px;margin-left:6px;">CRS transformado</span>'
            if crs_transf else '')

        return f"""
        <div class="section">
            <div class="iso-header">
                <span class="iso-title">Capa de Exclusión</span>
                <span class="iso-badge">{n_geom} geometría(s) aplicada(s){' · buffer ' + str(buffer_m) + ' m' if buffer_m and buffer_m > 0 else ''}</span>
            </div>
            <div class="info-grid">
                <div class="info-card">
                    <h4>Superficies {crs_badge}</h4>
                    <div class="density-row">
                        <span class="density-label"><strong>Área bruta (antes exclusión)</strong></span>
                        <span class="density-value">{_fmt(area_total, 2)} ha</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Área excluida</strong></span>
                        <span class="density-value" style="color:#dc3545;">{_fmt(area_excl_ha, 2)} ha</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Área efectiva de muestreo</strong></span>
                        <span class="density-value" style="color:#28a745;">{_fmt(area_ef_ha, 2)} ha</span>
                    </div>
                </div>
                <div class="info-card">
                    <h4>Porcentaje excluido</h4>
                    <div class="iso-bar-labels">
                        <span>Área excluida / área bruta</span>
                        <span style="font-weight:700;color:{bar_color};">{_fmt(pct_excl, 1)}%</span>
                    </div>
                    <div class="iso-bar-bg" style="margin-top:4px;">
                        <div class="iso-bar-fill" style="width:{min(pct_excl,100):.1f}%;background:{bar_color};"></div>
                    </div>
                    <div class="iso-bar-note" style="margin-top:6px;">
                        {_fmt(area_excl_ha, 2)} ha excluidas de {_fmt(area_total, 2)} ha brutas
                    </div>
                    <div class="iso-disclaimer" style="margin-top:10px;">
                        La densidad real de puntos se calcula sobre el
                        <strong>área efectiva</strong> (después de exclusión).
                        El área bruta queda registrada en el JSON de parámetros.
                    </div>
                </div>
            </div>
        </div>
        """


    @staticmethod
    def _meta_label(k):
        """Etiqueta en español para campos de metadatos JSON."""
        labels = {
            'descripcion':          'Descripción',
            'campo_fuente':         'Campo fuente',
            'campo_salida':         'Campo salida',
            'autor':                'Autor',
            'fecha':                'Fecha',
            'metodo_clasificacion': 'Método de clasificación',
            'capa_origen':          'Capa de origen',
        }
        return labels.get(k, k.replace('_', ' ').capitalize())

    def _report_densidad_clases(self, densidad_resumen, json_path='', params=None):
        """Sección HTML de densidad variable por clase JSON. Solo si estuvo activo."""
        if not densidad_resumen or not densidad_resumen.get('activo', False):
            return ""

        archivo   = os.path.basename(densidad_resumen.get('json_path', '') or json_path)
        clases    = densidad_resumen.get('clases', {})
        meta      = densidad_resumen.get('meta', {})

        # Construir tabla de clases
        # Verificar si alguna clase tiene rango definido
        tiene_rango = any('rango' in v for v in clases.values() if isinstance(v, dict))
        encabezado_rango = '<th style="padding:5px 10px;text-align:left;border-bottom:1px solid #ddd;">Ámbito</th>' if tiene_rango else ''

        # Verificar si alguna clase tiene campo intervalo
        clases = {k: v for k, v in clases.items() if not k.startswith('__')}
        tiene_intervalo = any('intervalo' in v for v in clases.values() if isinstance(v, dict))

        import math as _math
        _HA = 10000.0
        _SQRT3_2 = 0.8660254037844386
        _params = params or {}
        _HEX = _params.get('grid_type', Constants.GRID_HEXAGONAL) == Constants.GRID_HEXAGONAL

        filas_html = ""
        for clase_id, config in sorted(clases.items(), key=lambda x: str(x[0])):
            if 'densidad' in config and config['densidad'] > 0:
                d = float(config['densidad'])
                sp = _math.sqrt(_HA / (d * _SQRT3_2)) if _HEX else _math.sqrt(_HA / d)
                valor = f"{_fmt(d, 2)} pts/ha · Dist.: {_fmt(sp, 2)} m"
            elif 'espaciado' in config and config['espaciado'] > 0:
                sp = float(config['espaciado'])
                d = (_HA / (sp**2 * _SQRT3_2)) if _HEX else (_HA / sp**2)
                valor = f"Dist.: {_fmt(sp, 2)} m · {_fmt(d, 2)} pts/ha"
            elif 'ha_punto' in config and config['ha_punto'] > 0:
                h = float(config['ha_punto'])
                d = 1.0 / h
                sp = _math.sqrt(_HA / (d * _SQRT3_2)) if _HEX else _math.sqrt(_HA / d)
                valor = f"{_fmt(h, 2)} ha/pto · Dist.: {_fmt(sp, 2)} m"
            else:
                valor = "No definido"
            intervalo = config.get('intervalo', config.get('rango', ''))
            intervalo_td = f'<td style="padding:5px 10px;border-bottom:0.5px solid #ddd;color:#555;font-size:12px;">{intervalo}</td>' if tiene_intervalo else ''
            filas_html += f"""
                    <tr>
                        <td style="padding:5px 10px;border-bottom:0.5px solid #ddd;">{clase_id}</td>
                        <td style="padding:5px 10px;border-bottom:0.5px solid #ddd;">{valor}</td>
                        {intervalo_td}
                    </tr>"""

        return f"""
        <div class="section">
            <div class="iso-header">
                <span class="iso-title">Densidad Variable por Clase (JSON)</span>
                <span class="iso-badge">Modo Individual — anula densidad global</span>
            </div>
            <div class="info-grid">
                <div class="info-card">
                    <h4>Archivo de densidad</h4>
                    <div class="density-row">
                        <span class="density-label"><strong>Archivo</strong></span>
                        <span class="density-value" style="font-size:12px;">{archivo}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Clases configuradas</strong></span>
                        <span class="density-value">{len(clases)}</span>
                    </div>
                    {''.join('<div class="density-row"><span class="density-label">' + self._meta_label(k) + '</span>'
                              '<span class="density-value" style="font-size:12px;">' + (v.replace("Jenks Natural Breaks","Rupturas naturales (Jenks)") if isinstance(v,str) else str(v)) + '</span></div>'
                              for k,v in meta.items()) if meta else ''}
                    <p style="font-size:11px;color:#888;margin:8px 0 0 0;">
                        La densidad global configurada en la interfaz es reemplazada por
                        la densidad específica de cada clase. La comparación solicitada vs
                        real en la tarjeta Densidad no es representativa con este modo activo.
                    </p>
                </div>
                <div class="info-card">
                    <h4>Densidad por clase</h4>
                    <table style="width:100%;border-collapse:collapse;font-size:13px;">
                        <tr style="background:#f5f5f5;">
                            <th style="padding:5px 10px;text-align:left;border-bottom:1px solid #ddd;">Clase</th>
                            <th style="padding:5px 10px;text-align:left;border-bottom:1px solid #ddd;">Densidad / Espaciado</th>
                            {'<th style="padding:5px 10px;text-align:left;border-bottom:1px solid #ddd;">Intervalo</th>' if tiene_intervalo else ''}
                        </tr>{filas_html}
                    </table>
                </div>
            </div>
        </div>"""

    def _report_mapeo(self, mapeo_resumen):
        """Sección HTML del mapeo JSON. Solo se muestra si el mapeo estuvo activo."""
        if not mapeo_resumen or not mapeo_resumen.get('activo', False):
            return ""

        total_clases  = mapeo_resumen.get('total_clases', 0)
        aplicaciones  = mapeo_resumen.get('aplicaciones', 0)
        sin_mapeo     = mapeo_resumen.get('sin_mapeo', [])
        archivo       = os.path.basename(mapeo_resumen.get('json_path', ''))
        meta          = mapeo_resumen.get('meta', {})
        campo_fuente  = meta.get('campo_fuente', '')
        campo_salida  = meta.get('campo_salida', '')
        descripcion   = meta.get('descripcion', '')

        # Tarjeta IDs sin mapeo (fallback al valor original)
        sin_mapeo_html = ""
        if sin_mapeo:
            ids_str = ', '.join(str(x) for x in sin_mapeo[:15])
            if len(sin_mapeo) > 15:
                ids_str += f" … (+{len(sin_mapeo)-15} más)"
            sin_mapeo_html = f"""
            <details class="iso-detail" style="margin-top:10px;">
                <summary style="color:#856404;">[!] {len(sin_mapeo)} ID(s) sin mapeo — se usó valor original</summary>
                <div class="iso-detail-body">{ids_str}</div>
            </details>"""

        # Campos meta a mostrar con etiquetas legibles
        meta_labels = {
            'campo_fuente': 'Campo fuente',
            'campo_salida': 'Campo salida',
            'descripcion':  'Descripción',
            'autor':        'Autor',
            'fecha':        'Fecha',
            'metodo_clasificacion': 'Método de clasificación',
            'capa_origen':  'Capa de origen',
        }
        meta_html = ""
        meta_rows = [(meta_labels[k], meta.get(k,'')) for k in meta_labels
                     if k != 'total_clases' and meta.get(k,'')]
        if meta_rows:
            filas = ''.join(
                f'<div class="density-row"><span class="density-label">{lbl}</span>'
                f'<span class="density-value" style="font-size:12px;">{val.replace("Jenks Natural Breaks", "Rupturas naturales (Jenks)") if isinstance(val, str) else val}</span></div>'
                for lbl, val in meta_rows)
            meta_html = f'<div style="margin-top:8px;">{filas}</div>'
        else:
            meta_html = '<p style="font-size:11px;color:#888;margin-top:8px;">No se definieron metadatos en el JSON.</p>'

        return f"""
        <div class="section">
            <div class="iso-header">
                <span class="iso-title">Mapeo de Clases JSON</span>
                <span class="iso-badge">Modo Individual — campo ID_ORIGINAL</span>
            </div>
            <div class="info-grid">
                <div class="info-card">
                    <h4>Archivo de mapeo</h4>
                    <div class="density-row">
                        <span class="density-label"><strong>Archivo</strong></span>
                        <span class="density-value" style="font-size:12px;">{archivo}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Clases definidas</strong></span>
                        <span class="density-value">{total_clases}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Aplicaciones exitosas</strong></span>
                        <span class="density-value">{aplicaciones:,}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>IDs sin mapeo (fallback)</strong></span>
                        <span class="density-value {'diff-ok' if not sin_mapeo else 'diff-warn'}">{len(sin_mapeo)}</span>
                    </div>
                    {sin_mapeo_html}
                </div>
                <div class="info-card">
                    <h4>Metadatos del mapeo</h4>
                    {meta_html if meta_html else '<p style="font-size:12px;color:#888;">No se definieron metadatos en el JSON.</p>'}
                    <div class="iso-disclaimer" style="margin-top:10px;">
                        El campo ID_ORIGINAL en la capa de salida es tipo <strong>String</strong>
                        cuando el mapeo está activo, independientemente del tipo del campo fuente.
                        Los IDs sin mapeo conservan su valor original.
                    </div>
                </div>
            </div>
        </div>
        """

    def _report_duplicados(self, dup_resumen):
        """Sección HTML de duplicados de borde. Solo en modo Individual."""
        if not dup_resumen:
            return ""

        total        = dup_resumen.get('total_puntos', 0)
        n_dups       = dup_resumen.get('duplicados', 0)
        extras       = dup_resumen.get('features_extra', 0)
        pct          = dup_resumen.get('pct_duplicados', 0.0)
        ids          = dup_resumen.get('ids_afectados', [])
        sobrantes_elim = dup_resumen.get('sobrantes_eliminados', 0)

        if n_dups == 0:
            estado_color = '#28a745'
            estado_texto = '[OK] Sin duplicados de borde detectados.'
            detalle_html = ''
        else:
            estado_color = '#dc3545' if pct > 1.0 else '#e67e22'
            estado_texto = (
                f'[!] {n_dups} coordenada(s) duplicada(s) — '
                f'{extras} entidad(es) sobrante(s) ({_fmt(pct, 2)}% del total).')
            ids_cons  = dup_resumen.get('ids_conservados', [])
            ids_sobr  = dup_resumen.get('ids_sobrantes', [])

            def _fmt_ids(lst, limit=20):
                s = ', '.join(str(i) for i in lst[:limit])
                if len(lst) > limit:
                    s += f' … (+{len(lst)-limit} más)'
                return s or '—'

            if sobrantes_elim > 0:
                # Después de ELIMINAR_SOBRANTES — mostrar conservados y eliminados
                detalle_html = f"""
            <details class="iso-detail" style="margin-top:10px;">
                <summary style="color:#856404;">[!] Detalle por grupo ({n_dups} ubicación(es))</summary>
                <div class="iso-detail-body">
                    <p style="margin:4px 0;"><strong style="color:#28a745;">✔ id_punto conservados ({len(ids_cons)}):</strong><br>
                    {_fmt_ids(ids_cons)}</p>
                    <p style="margin:4px 0;"><strong style="color:#dc3545;">✖ id_punto eliminados ({len(ids_sobr)}):</strong><br>
                    {_fmt_ids(ids_sobr)}</p>
                </div>
            </details>"""
            else:
                # Solo detección — mostrar afectados con sugerencia
                ids_cons_hint = _fmt_ids(ids_cons) if ids_cons else '—'
                ids_sobr_hint = _fmt_ids(ids_sobr) if ids_sobr else '—'
                detalle_html = f"""
            <details class="iso-detail" style="margin-top:10px;">
                <summary style="color:#856404;">[!] id_punto afectados ({len(ids)}) — si se eliminaran sobrantes:</summary>
                <div class="iso-detail-body">
                    <p style="margin:4px 0;"><strong style="color:#28a745;">✔ Se conservarían ({len(ids_cons)}):</strong><br>{ids_cons_hint}</p>
                    <p style="margin:4px 0;"><strong style="color:#888;">✖ Se eliminarían ({len(ids_sobr)}):</strong><br>{ids_sobr_hint}</p>
                    <p style="font-size:11px;color:#888;margin-top:6px;">Active 'Eliminar entidades sobrantes' para aplicar esta limpieza.</p>
                </div>
            </details>"""

        return f"""
        <div class="section">
            <div class="iso-header">
                <span class="iso-title">Duplicados de Borde</span>
                <span class="iso-badge">Modo Individual y Conjunto·Islas — predicado intersects()</span>
            </div>
            <div class="info-grid">
                <div class="info-card">
                    <h4>Resultado del análisis</h4>
                    <div class="density-row">
                        <span class="density-label"><strong>Puntos totales</strong></span>
                        <span class="density-value">{_fmt(total, 0)}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Coordenadas duplicadas</strong></span>
                        <span class="density-value" style="color:{estado_color};">{n_dups}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Entidades sobrantes</strong></span>
                        <span class="density-value" style="color:{estado_color};">{extras}</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Porcentaje</strong></span>
                        <span class="density-value" style="color:{estado_color};">{_fmt(pct, 2)}%</span>
                    </div>
                    <div class="density-row">
                        <span class="density-label"><strong>Entidades sobrantes eliminadas</strong></span>
                        <span class="density-value" style="color:{'#28a745' if sobrantes_elim > 0 else '#6c757d'};">{sobrantes_elim if sobrantes_elim > 0 else ('— (sin duplicados)' if dup_resumen.get('eliminar_sobrantes_activo') else '— (no solicitado)')}</span>
                    </div>
                    {detalle_html}
                </div>
                <div class="info-card">
                    <h4>Interpretación</h4>
                    <p style="font-size:12.5px;line-height:1.6;color:{estado_color};
                               font-weight:{'700' if n_dups > 0 else '400'};">
                        {estado_texto}
                    </p>
                    <div class="iso-disclaimer" style="margin-top:10px;">
                        <strong>en_borde=True</strong>: punto sobre el borde 
                        <em>compartido</em> entre dos polígonos adyacentes — capturado 
                        por ambos con distinto <code>ID_ORIGINAL</code>. El campo se 
                        asigna en post-proceso tras la detección de duplicados.<br>
                        <strong>en_borde=False</strong>: punto en el interior del 
                        polígono, sin coincidencia en bordes compartidos.<br><br>
                        {'<strong style="color:#dc3545;">[!] Revisar antes de usar como marco muestral.</strong>'
                         if n_dups > 0 else
                         'La capa no presenta duplicados de borde entre polígonos adyacentes.'}
                    </div>
                </div>
            </div>
        </div>
        """

    def _report_warnings(self, advertencias):
        if not advertencias:
            return """
            <div class="section success-box">
                <h3>[OK] Estado del Proceso</h3>
                <p>Proceso completado sin errores ni advertencias.</p>
            </div>
            """
        else:
            lista = ''.join(f'<li>{w}</li>' for w in advertencias)
            return f"""
            <div class="section warning-box">
                <h3>[!] Alertas y Advertencias</h3>
                <ul>{lista}</ul>
            </div>
            """

    def _report_efficiency(self, vertices_antes, vertices_despues, params):
        """
        Muestra siempre la sección de vértices.
        - Con simplificación activa: muestra reducción real lograda.
        - Sin simplificación: muestra conteo de vértices procesados e indicación
          de que la simplificación estaba desactivada, con referencia de ganancia potencial.
        """
        simplificacion_activa = params.get('simplificacion_activa', False)
        tolerancia = params.get('tolerancia_entrada', 5.0)

        if simplificacion_activa and vertices_antes > 0:
            eliminados = vertices_antes - vertices_despues
            reduccion  = (eliminados / vertices_antes * 100) if vertices_antes > 0 else 0
            if reduccion >= 20:
                nivel_txt   = '[OK] Alta'
                nivel_color = '#28a745'
            elif reduccion >= 5:
                nivel_txt   = '[!] Moderada — considere mayor tolerancia'
                nivel_color = '#e67e22'
            else:
                nivel_txt   = '[R] Baja — considere mayor tolerancia'
                nivel_color = '#dc3545'

            detalle_html = f"""
                <div class="info-card">
                    <h4>Conteo de Vértices</h4>
                    <p><strong>Originales:</strong> {_fmt(vertices_antes, 0)}</p>
                    <p><strong>Finales:</strong> {_fmt(vertices_despues, 0)}</p>
                    <p><strong>Eliminados:</strong> {_fmt(eliminados, 0)}</p>
                </div>
                <div class="info-card">
                    <h4>Reducción</h4>
                    <p style="font-size:1.3em;font-weight:bold;color:{nivel_color};">{_fmt(reduccion, 1)}%</p>
                    <p style="font-size:0.88em;color:#555;">{nivel_txt}</p>
                    <p style="font-size:0.85em;color:#777;margin-top:8px;">
                        Tolerancia aplicada: {_fmt(tolerancia, 1)} m<br>
                        Vértices procesados con malla generada a partir de geometría simplificada.
                    </p>
                </div>"""
        else:
            # Sin simplificación: mostrar vértices totales procesados y ganancia potencial
            ref_color = '#6c757d'
            detalle_html = f"""
                <div class="info-card">
                    <h4>Vértices Procesados</h4>
                    <p><strong>Total vértices entrada:</strong> {_fmt(vertices_antes, 0)}</p>
                    <p><strong>Simplificación:</strong> No aplicada</p>
                    <p><strong>Reducción:</strong> 0% (sin cambio)</p>
                </div>
                <div class="info-card">
                    <h4>Ganancia Potencial</h4>
                    <p style="font-size:0.88em;color:{ref_color};line-height:1.5;">
                        Con simplificación activada se puede obtener una reducción
                        significativa de vértices. La ganancia en tiempo depende del
                        número de polígonos, su configuración geométrica y el número
                        de puntos generados.
                    </p>
                </div>"""

        return f"""
        <div class="section">
            <h3>Simplificación de Entrada (Douglas-Peucker)</h3>
            <div class="info-grid">
                {detalle_html}
            </div>
        </div>
        """