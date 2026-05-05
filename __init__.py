# -*- coding: utf-8 -*-
"""
Crear Malla de Puntos — Plugin de Processing para QGIS
Autor: Jorge Fallas (jfallas56@gmail.com)
"""

def classFactory(iface):
    from .malla_puntos_plugin import MallaPuntosPlugin
    return MallaPuntosPlugin(iface)
