# -*- coding: utf-8 -*-
"""
Clase principal del complemento Crear Malla de Puntos.
Registra el provider de Processing y añade un botón en la barra de herramientas.
"""

import os
from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QToolBar
from qgis import processing
from .malla_puntos_provider import MallaPuntosProvider


class MallaPuntosPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.toolbar = None
        self.action = None

    def initProcessing(self):
        self.provider = MallaPuntosProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

        # --- Barra de herramientas ---
        self.toolbar = QToolBar('Malla de Puntos')
        self.toolbar.setObjectName('MallaPuntosToolbar')
        self.iface.mainWindow().addToolBar(self.toolbar)

        icon_path = os.path.join(os.path.dirname(__file__), 'icons', 'icon.svg')
        icon = QIcon(icon_path)

        self.action = QAction(icon, 'Crear Malla de Puntos', self.iface.mainWindow())
        self.action.setToolTip(
            'Crear Malla de Puntos\n'
            'Genera una malla sistemática de puntos (hexagonal o rectangular)\n'
            'dentro de polígonos.'
        )
        self.action.triggered.connect(self._open_dialog)
        self.toolbar.addAction(self.action)

        # También en el menú Complementos
        self.iface.addPluginToMenu('Malla de Puntos', self.action)

    def _open_dialog(self):
        """Abre el diálogo del algoritmo en Processing."""
        processing.execAlgorithmDialog('mallapuntos:crearmallapuntos_reporte')

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        if self.toolbar:
            self.toolbar.deleteLater()
        if self.action:
            self.iface.removePluginMenu('Malla de Puntos', self.action)
