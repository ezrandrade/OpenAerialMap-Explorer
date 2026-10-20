"""QGIS plugin entrypoint."""
def classFactory(iface):
    from .plugin import OpenAerialMapExplorer
    return OpenAerialMapExplorer(iface)
