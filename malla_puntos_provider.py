# -*- coding: utf-8 -*-
"""
Provider de Processing para el complemento Crear Malla de Puntos.
Registra todos los algoritmos del complemento en el panel de Processing de QGIS.
"""

import os
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
from .malla_puntos_algorithm import CrearMallaPuntos


class MallaPuntosProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(CrearMallaPuntos())

    def id(self):
        return 'mallapuntos'

    def name(self):
        return 'Herramientas Malla de Puntos'

    def longName(self):
        return self.name()

    def icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'icons', 'icon.svg')
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return super().icon()
