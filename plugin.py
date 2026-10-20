"""OpenAerialMap Explorer v0.5.3 — QGIS 4 / Qt6.

Uses HOT's live STAC catalog and TiTiler (individual tiles with alpha/NoData).
"""
import json
import math
import os
import re
from pathlib import Path
from datetime import date
from html import escape
from urllib.parse import quote, urlencode

from qgis.PyQt.QtCore import Qt, QUrl, QDate, QIODevice, QTimer, QCoreApplication, QFile, QStandardPaths, QSettings
from qgis.PyQt.QtGui import QAction, QPixmap, QDesktopServices, QIcon
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.PyQt.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QTabWidget, QTextBrowser,
    QAbstractItemView, QComboBox, QCheckBox, QDateEdit, QDoubleSpinBox,
    QGroupBox, QFormLayout, QScrollArea, QFileDialog, QProgressBar
)
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsNetworkAccessManager, QgsVectorLayer,
    QgsFeature, QgsGeometry, QgsPointXY, QgsField, QgsWkbTypes, QgsFillSymbol, QgsRectangle,
)

BASEMAP = 'https://global.imagery.hotosm.org/{z}/{x}/{y}.png'
SEARCH_API = 'https://api.imagery.hotosm.org/stac/search'
ITEM_TILES = ('https://api.imagery.hotosm.org/raster/collections/openaerialmap/items/'
              '{id}/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}?assets=visual')
MAX_ITEMS = 100


def make_xyz_layer(url, name):
    # Encode URL query delimiters so QGIS XYZ datasource parsing does not
    # misread TiTiler's &assets / &nodata as provider options.
    uri = 'type=xyz&url=' + quote(url, safe=':/{}%,') + '&zmin=0&zmax=22'
    return QgsRasterLayer(uri, name, 'wms')


def valid_date(text):
    if not text:
        return ''
    try:
        return date.fromisoformat(str(text)[:10]).isoformat()
    except (ValueError, TypeError):
        return ''


def image_summary(item):
    p = item.get('properties') or {}
    return (str(p.get('title') or item['id']),
            valid_date(p.get('datetime') or p.get('start_datetime')) or 'Data n/d',
            p.get('gsd'), str(p.get('oam:platform_type') or 'n/d'))


def filename_for_item(item):
    """Prefer the STAC title, falling back to the image identifier.

    Sanitize names for Windows, Linux and macOS without discarding accents.
    """
    properties = item.get('properties') or {}
    title = properties.get('title') if isinstance(properties, dict) else None
    raw = title if isinstance(title, str) and title.strip() else str(item.get('id') or 'imagem')
    # Remove controls and filesystem-reserved characters; collapse whitespace.
    cleaned = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', raw)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' ._')
    cleaned = cleaned[:110].rstrip(' ._') or 'imagem'
    # Windows forbids these names even when followed by extensions.
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', cleaned, re.I):
        cleaned = '_' + cleaned
    return cleaned + '.tif'


def footprint_from_item(item):
    """Convert STAC GeoJSON polygons to QGIS 4 geometries without fromGeoJson.

    Preserve outer rings, holes and multipart polygons. Bad or missing geometries
    are ignored; the separate STAC bbox still supports the zoom command.
    """
    geometry = item.get('geometry')
    if not isinstance(geometry, dict):
        return None
    geometry_type = geometry.get('type')
    coords = geometry.get('coordinates')
    if not isinstance(coords, list):
        return None

    def build_ring(points):
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError('Invalid ring')
        ring = []
        for pair in points:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                raise ValueError('Invalid position')
            x, y = float(pair[0]), float(pair[1])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('Non-finite coordinate')
            ring.append(QgsPointXY(x, y))
        if ring[0] != ring[-1]:
            ring.append(QgsPointXY(ring[0].x(), ring[0].y()))
        if len(ring) < 4:
            raise ValueError('Invalid closed ring')
        return ring

    def build_polygon(poly):
        if not isinstance(poly, list) or not poly:
            raise ValueError('Invalid polygon')
        return [build_ring(ring) for ring in poly]

    try:
        if geometry_type == 'Polygon':
            geom = QgsGeometry.fromPolygonXY(build_polygon(coords))
        elif geometry_type == 'MultiPolygon':
            geom = QgsGeometry.fromMultiPolygonXY([build_polygon(poly) for poly in coords])
        else:
            return None
        return geom if geom is not None and not geom.isNull() and not geom.isEmpty() else None
    except (ValueError, TypeError, OverflowError):
        return None


class OAMWindow(QDialog):
    def __init__(self, plugin):
        super().__init__(plugin.iface.mainWindow(), Qt.WindowType.Window)
        self.setWindowTitle('OpenAerialMap Explorer')
        self.setModal(False)
        self.plugin = plugin
        self.setObjectName('OpenAerialMapExplorerWindow')
        self.setMinimumSize(360, 360)
        self.items = []
        self.reply = None
        self.thumbnail_reply = None
        self.preview_urls = []
        self.preview_attempt = 0
        self.preview_request_generation = 0
        self.preview_failure = ""
        self.coverage_layer_id = None
        self.download_reply = None
        self.download_file = None
        self.download_temp_path = None
        self.download_final_path = None
        self.download_error = ''
        self.download_aborted = False
        self.download_original_url = ''
        self.batch_queue = []
        self.batch_running = False
        self.batch_cancel_requested = False
        self.batch_history = []
        self.active_batch_job = None
        self.batch_add_layers = True
        self.query_rectangle = None
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        title = QLabel('OpenAerialMap Explorer')
        title.setStyleSheet('font-size: 16px; font-weight: 600;')
        outer.addWidget(title)
        tabs = QTabWidget()
        outer.addWidget(tabs, 1)

        base = QWidget()
        base_layout = QVBoxLayout(base)
        note = QLabel('Mosaico global: em escalas menores (zoom 0–13), exibe grade de cobertura; imagens no zoom 14 ou superior.')
        note.setWordWrap(True)
        base_layout.addWidget(note)
        add_base = QPushButton('Adicionar mapa-base OAM')
        add_base.clicked.connect(plugin.add_basemap)
        base_layout.addWidget(add_base)
        base_layout.addStretch(1)
        tabs.addTab(base, 'Mapa-base')

        explore = QWidget()
        exp = QVBoxLayout(explore)
        filters = QGroupBox('Filtros da pesquisa')
        fields = QFormLayout(filters)
        self.use_date = QCheckBox('Filtrar por período')
        fields.addRow(self.use_date)
        self.from_date = QDateEdit()
        self.from_date.setCalendarPopup(True)
        self.from_date.setDisplayFormat('dd/MM/yyyy')
        self.from_date.setDate(QDate(2000, 1, 1))
        fields.addRow('De', self.from_date)
        self.to_date = QDateEdit()
        self.to_date.setCalendarPopup(True)
        self.to_date.setDisplayFormat('dd/MM/yyyy')
        self.to_date.setDate(QDate.currentDate())
        fields.addRow('Até', self.to_date)
        self.platform = QComboBox()
        self.platform.addItem('Todas', '')
        for value, name in [('uav', 'Drone (UAV)'), ('aircraft', 'Aeronave'),
                            ('satellite', 'Satélite'), ('kite', 'Pipa'),
                            ('balloon', 'Balão')]:
            self.platform.addItem(name, value)
        fields.addRow('Plataforma', self.platform)
        self.max_gsd = QDoubleSpinBox()
        self.max_gsd.setDecimals(2)
        self.max_gsd.setRange(0.0, 10000.0)
        self.max_gsd.setValue(0.0)
        self.max_gsd.setSuffix(' m/pixel')
        self.max_gsd.setSpecialValueText('Sem limite')
        fields.addRow('Resolução máxima', self.max_gsd)
        exp.addWidget(filters)

        source_box = QGroupBox('Área de pesquisa')
        source_layout = QVBoxLayout(source_box)
        search = QPushButton('Pesquisar na extensão visível do mapa')
        search.clicked.connect(lambda: self.search('canvas'))
        source_layout.addWidget(search)
        self.layer_combo = QComboBox()
        self.layer_combo.setToolTip('Escolha uma camada vetorial ou raster; será usada sua extensão geográfica.')
        source_layout.addWidget(self.layer_combo)
        layer_buttons = QHBoxLayout()
        refresh = QPushButton('Atualizar camadas')
        refresh.clicked.connect(self.refresh_layers)
        layer_buttons.addWidget(refresh)
        search_layer = QPushButton('Pesquisar pela camada')
        search_layer.clicked.connect(lambda: self.search('layer'))
        layer_buttons.addWidget(search_layer)
        source_layout.addLayout(layer_buttons)
        exp.addWidget(source_box)
        self.refresh_layers()
        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Use an independently scrolling, compact results list. Avoid stretching
        # this widget to fill the entire Explorer window when only a few items
        # are returned. The list remains scrollable for large query results.
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setMinimumHeight(105)
        self.results.setMaximumHeight(165)
        self.results.setToolTip('Use Ctrl ou Shift para selecionar várias imagens. Role para ver mais resultados.')
        self.results.currentRowChanged.connect(self.describe)
        self.results.itemDoubleClicked.connect(lambda *_: self.add_selected())
        # Results and preview share one horizontal band, reducing overall height.
        # The list scrolls independently while the image keeps a stable footprint.
        result_band = QHBoxLayout()
        result_band.setSpacing(8)
        result_col = QVBoxLayout()
        result_col.setSpacing(3)
        result_col.addWidget(QLabel('Resultados da pesquisa:'))
        result_col.addWidget(self.results)
        result_band.addLayout(result_col, 3)
        preview_col = QVBoxLayout()
        preview_col.setSpacing(3)
        preview_col.addWidget(QLabel('Prévia da imagem:'))
        self.thumbnail = QLabel('Prévia indisponível')
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setWordWrap(True)
        self.thumbnail.setMinimumSize(140, 105)
        self.thumbnail.setMaximumHeight(165)
        self.thumbnail.setStyleSheet('QLabel { background: palette(base); border: 1px solid palette(mid); border-radius: 4px; }')
        preview_col.addWidget(self.thumbnail, 1)
        result_band.addLayout(preview_col, 2)
        exp.addLayout(result_band)
        self.details = QTextBrowser()
        self.details.setMinimumHeight(68)
        self.details.setMaximumHeight(112)
        self.details.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.details.setOpenExternalLinks(False)
        exp.addWidget(self.details)
        buttons = QHBoxLayout()
        add = QPushButton('Adicionar imagem')
        add.clicked.connect(self.add_selected)
        buttons.addWidget(add)
        go = QPushButton('Ir para a imagem')
        go.clicked.connect(self.zoom_selected)
        buttons.addWidget(go)
        coverage = QPushButton('Exibir coberturas')
        coverage.clicked.connect(self.show_coverages)
        buttons.addWidget(coverage)
        exp.addLayout(buttons)
        download_row = QHBoxLayout()
        self.download_button = QPushButton('Download da Imagem Original')
        self.download_button.setToolTip('Salva o GeoTIFF diretamente na pasta escolhida, sem abrir o navegador.')
        self.download_button.clicked.connect(self.download_original)
        download_row.addWidget(self.download_button, 1)
        self.cancel_button = QPushButton('Cancelar')
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.setEnabled(False)
        download_row.addWidget(self.cancel_button)
        exp.addLayout(download_row)
        batch_row = QHBoxLayout()
        self.batch_button = QPushButton('Baixar imagens selecionadas (fila)')
        self.batch_button.setToolTip('Use Ctrl/Shift na lista para selecionar imagens; transferência sequencial.')
        self.batch_button.clicked.connect(self.download_batch)
        batch_row.addWidget(self.batch_button, 1)
        self.cancel_queue_button = QPushButton('Cancelar fila')
        self.cancel_queue_button.setEnabled(False)
        self.cancel_queue_button.clicked.connect(self.cancel_batch)
        batch_row.addWidget(self.cancel_queue_button)
        exp.addLayout(batch_row)
        self.batch_history_list = QListWidget()
        self.batch_history_list.setMaximumHeight(95)
        self.batch_history_list.setToolTip('Histórico desta sessão: concluído, cancelado ou erro.')
        exp.addWidget(self.batch_history_list)
        self.add_after_download = QCheckBox('Adicionar ao QGIS após o download')
        self.add_after_download.setChecked(True)
        exp.addWidget(self.add_after_download)
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_progress.setFormat('Nenhum download em andamento')
        self.download_progress.setMinimumHeight(18)
        exp.addWidget(self.download_progress)
        self.nodata_mode = QComboBox()
        self.nodata_mode.addItem('Automático — usar máscara/alpha original', None)
        self.nodata_mode.addItem('Fundo branco — considerar 255 como NoData', 255)
        self.nodata_mode.addItem('Fundo preto — considerar 0 como NoData', 0)
        self.nodata_mode.setToolTip('Os valores 0 e 255 são enviados ao renderizador remoto. Use somente se o fundo for realmente NoData; pixels válidos com esse valor também podem ficar transparentes.')
        exp.addWidget(QLabel('Transparência de fundo de imagens individuais:'))
        exp.addWidget(self.nodata_mode)
        exp.addStretch(1)
        tabs.addTab(explore, 'Explorar')

        self.status = QLabel('Pronto. Pesquise na extensão atual do mapa.')
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        # Use a scrollable, independent window instead of a dock widget.
        # This keeps the QGIS map canvas and main-window dimensions unchanged.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(central)
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(4, 4, 4, 4)
        window_layout.addWidget(scroll)
        # Persistent footer: the close button remains visible while scrolling.
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = QPushButton('Fechar')
        self.close_button.setToolTip('Fechar a janela do OpenAerialMap Explorer')
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        window_layout.addLayout(footer)
        self.resize(650, 590)
        self._centered_once = False

    def center_on_screen(self):
        if self._centered_once:
            return
        screen = self.plugin.iface.mainWindow().screen()
        if screen is None:
            screen = self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            width = min(650, max(360, area.width() - 40))
            height = min(640, max(360, area.height() - 60))
            self.resize(width, height)
            frame = self.frameGeometry()
            frame.moveCenter(area.center())
            self.move(frame.topLeft())
        self._centered_once = True

    def refresh_layers(self):
        selected = self.layer_combo.currentData() if self.layer_combo.count() else None
        self.layer_combo.clear()
        for layer in sorted(QgsProject.instance().mapLayers().values(), key=lambda obj: obj.name().lower()):
            if layer.isValid() and layer.isSpatial() and layer.crs().isValid():
                self.layer_combo.addItem(layer.name(), layer.id())
        if selected:
            idx = self.layer_combo.findData(selected)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)

    def search(self, source='canvas'):
        if self.reply is not None:
            self.status.setText('Pesquisa anterior ainda em andamento.')
            return
        try:
            if source == 'canvas':
                canvas = self.plugin.iface.mapCanvas()
                source_crs = canvas.mapSettings().destinationCrs()
                source_rect = canvas.extent()
                source_name = 'extensão visível'
            else:
                layer = QgsProject.instance().mapLayer(self.layer_combo.currentData())
                if layer is None or not layer.isValid():
                    raise ValueError('Selecione uma camada válida e atualize a lista.')
                source_crs = layer.crs()
                source_rect = layer.extent()
                source_name = 'limites da camada ' + layer.name()
            if source_rect.isNull() or source_rect.isEmpty():
                raise ValueError('A extensão selecionada está vazia.')
            if not source_crs.isValid():
                raise ValueError('O SRC da área de pesquisa não está definido.')
            xform = QgsCoordinateTransform(source_crs,
                                            QgsCoordinateReferenceSystem('EPSG:4326'),
                                            QgsProject.instance())
            rect = xform.transformBoundingBox(source_rect)
            west, south = max(-180., rect.xMinimum()), max(-90., rect.yMinimum())
            east, north = min(180., rect.xMaximum()), min(90., rect.yMaximum())
            if west >= east or south >= north:
                raise ValueError('Extensão geográfica inválida; confira o SRC e a área selecionada.')
            payload = {'bbox': [round(v, 7) for v in (west, south, east, north)],
                       'limit': MAX_ITEMS, 'collections': ['openaerialmap']}
            if self.use_date.isChecked():
                start = self.from_date.date().toString('yyyy-MM-dd')
                end = self.to_date.date().toString('yyyy-MM-dd')
                if start > end:
                    raise ValueError('A data inicial é posterior à final.')
                payload['datetime'] = start + 'T00:00:00Z/' + end + 'T23:59:59Z'
            req = QNetworkRequest(QUrl(SEARCH_API))
            req.setRawHeader(b'Accept', b'application/geo+json, application/json')
            req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
            self.results.clear()
            self.items = []
            self.details.clear()
            self.thumbnail.setText('Prévia indisponível')
            self.status.setText('Consultando catálogo STAC por ' + source_name + '…')
            body = json.dumps(payload).encode('utf-8')
            self.reply = QgsNetworkAccessManager.instance().post(req, body)
            self.reply.finished.connect(self.finish_search)
        except Exception as exc:
            self.status.setText('Não foi possível pesquisar: ' + str(exc))

    def finish_search(self):
        reply = self.reply
        self.reply = None
        if reply is None:
            return
        try:
            http_status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            raw = bytes(reply.readAll())  # Consume the response exactly once.
            status = int(http_status) if http_status is not None else None
            if status is not None and not 200 <= status < 300:
                detail = raw.decode('utf-8', errors='replace')[:350]
                raise RuntimeError(f'HTTP {status}: {reply.errorString()} — {detail}')
            if not raw:
                raise RuntimeError(f'Resposta vazia (HTTP {status}): {reply.errorString()}')
            try:
                payload = json.loads(raw.decode('utf-8-sig'))
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f'O catálogo não retornou JSON válido (HTTP {status}): {exc}') from exc
            if not isinstance(payload, dict):
                raise ValueError('Formato inesperado: esperava objeto GeoJSON/STAC.')
            features = payload.get('features')
            if not isinstance(features, list):
                message = payload.get('description') or payload.get('message') or payload.get('detail')
                raise ValueError('Resposta STAC sem lista de feições.' + (f' {str(message)[:150]}' if message else ''))
            # QGIS/Qt can report an opaque network error even after an HTTP 200
            # response containing a valid FeatureCollection. Trust the validated
            # HTTP success response and GeoJSON body in that case.
            if status is None and reply.error():
                raise RuntimeError('Falha de rede sem código HTTP: ' + reply.errorString())
            platform = self.platform.currentData()
            max_gsd = self.max_gsd.value()
            for item in features:
                if not isinstance(item, dict) or not item.get('id'):
                    continue
                p = item.get('properties') or {}
                if platform and p.get('oam:platform_type') != platform:
                    continue
                gsd = p.get('gsd')
                if max_gsd > 0:
                    try:
                        if gsd is None or float(gsd) > max_gsd:
                            continue
                    except (TypeError, ValueError):
                        continue
                self.items.append(item)
                name, dt, resolution, platform_name = image_summary(item)
                gsd_text = f' • {resolution} m/px' if resolution is not None else ''
                self.results.addItem(QListWidgetItem(f'{dt} • {platform_name}{gsd_text}\n{name[:85]}'))
            self.status.setText(f'{len(self.items)} resultado(s) após filtros; até {MAX_ITEMS} registros consultados. Refine a área para uma pesquisa mais completa.')
            if self.items:
                self.results.setCurrentRow(0)
        except Exception as exc:
            self.status.setText('Falha no catálogo: ' + str(exc))
        finally:
            reply.deleteLater()

    def describe(self, index):
        # Invalidate pending downloads when the user changes selection.
        self.preview_request_generation += 1
        if self.thumbnail_reply is not None:
            old = self.thumbnail_reply
            self.thumbnail_reply = None
            old.abort()
        self.preview_urls = []
        self.preview_attempt = 0
        self.preview_failure = ''
        self.thumbnail.clear()
        if index < 0 or index >= len(self.items):
            self.thumbnail.setText('Selecione uma imagem')
            self.details.clear()
            return
        self.thumbnail.setText('Carregando prévia…')
        item = self.items[index]
        p = item.get('properties') or {}
        a = item.get('assets') or {}
        license_id = p.get('license') or item.get('license') or 'não informada'
        rows = [('ID', item.get('id')), ('Título', p.get('title')),
                ('Data', p.get('datetime') or p.get('start_datetime')),
                ('Resolução (m/pixel)', p.get('gsd')),
                ('Plataforma', p.get('oam:platform_type')),
                ('Produtor', p.get('oam:producer_name')),
                ('Licença', license_id),
                ('GeoTIFF original', 'disponível' if (a.get('visual') or {}).get('href') else 'não informado')]
        self.details.setPlainText('\n'.join(f'{key}: {val if val is not None else "não informado"}' for key, val in rows))
        # The STAC thumbnail is optional. TiTiler supports both size-specific
        # /preview/{width}x{height}.png and /preview.png?max_size=... routes.
        # Prefer assets explicitly declared as previews by the STAC producer.
        for asset_name, asset in a.items():
            if not isinstance(asset, dict):
                continue
            roles = asset.get('roles') or []
            if asset_name in ('thumbnail', 'preview', 'overview') or 'thumbnail' in roles or 'overview' in roles:
                thumb = asset.get('href')
                if isinstance(thumb, str) and thumb.startswith('https://') and not thumb.lower().split('?')[0].endswith(('.tif', '.tiff')):
                    self.preview_urls.append(thumb)
        image_id = quote(str(item['id']), safe='')
        base = (f'https://api.imagery.hotosm.org/raster/collections/'
                f'openaerialmap/items/{image_id}/preview')
        self.preview_urls.extend((
            base + '/256x256.png?assets=visual',
            base + '.png?assets=visual&max_size=256',
            base + '/256x256.png?assets=visual&rescale=0,255',
        ))
        self.preview_urls = list(dict.fromkeys(self.preview_urls))
        self._request_next_preview(self.preview_request_generation)

    def _request_next_preview(self, generation):
        if generation != self.preview_request_generation:
            return
        if self.preview_attempt >= len(self.preview_urls):
            self.thumbnail.setText('Prévia indisponível\n' + self.preview_failure[:90])
            return
        url = self.preview_urls[self.preview_attempt]
        self.preview_attempt += 1
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b'Accept', b'image/png, image/jpeg, image/webp, */*')
        req.setAttribute(QNetworkRequest.Attribute.FollowRedirectsAttribute, True) if hasattr(QNetworkRequest.Attribute, 'FollowRedirectsAttribute') else None
        reply = QgsNetworkAccessManager.instance().get(req)
        self.thumbnail_reply = reply
        reply.finished.connect(self.finish_thumbnail)
        # Bound waiting per candidate so a stalled server does not trap the UI.
        QTimer.singleShot(12000, lambda r=reply, g=generation: self._preview_timeout(r, g))

    def _preview_timeout(self, reply, generation):
        if generation == self.preview_request_generation and reply is self.thumbnail_reply and not reply.isFinished():
            self.preview_failure = 'Tempo limite do serviço de prévias.'
            reply.abort()  # finish_thumbnail moves to next candidate.

    def finish_thumbnail(self):
        reply = self.sender()
        if reply is None:
            return
        current = reply is self.thumbnail_reply
        if current:
            self.thumbnail_reply = None
        try:
            if not current:
                return
            status_raw = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            status = int(status_raw) if status_raw is not None else None
            data = bytes(reply.readAll())
            content_type = str(reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader) or '')
            network_error = reply.errorString() or 'erro não detalhado'
            pix = QPixmap()
            # Some Qt/QGIS installations report a spurious network warning
            # even after receiving a complete HTTP 200 image. Validate the
            # HTTP status and the actual raster bytes rather than error() alone.
            valid_http = status is not None and 200 <= status < 300
            if valid_http and 0 < len(data) <= 8_000_000 and pix.loadFromData(data):
                self.thumbnail.setPixmap(pix.scaled(max(140, self.thumbnail.width() - 8),
                                                   max(90, self.thumbnail.height() - 8),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                self.thumbnail.setToolTip('Prévia carregada do catálogo/serviço OAM.')
                return
            if status is not None and status >= 400:
                reason = f'HTTP {status}'
            elif not data:
                reason = f'Sem dados (HTTP {status if status is not None else "?"}; {network_error})'
            elif len(data) > 8_000_000:
                reason = 'Resposta excessivamente grande'
            else:
                reason = f'Formato inválido (HTTP {status if status is not None else "?"}; {content_type or "tipo desconhecido"})'
                if data[:1] in (b'{', b'<'):
                    reason += ': ' + data[:160].decode('utf-8', errors='replace').replace('\n', ' ')
            self.preview_failure = reason
            self.thumbnail.setToolTip('Última URL testada: ' + reply.url().toString() + '\n' + reason)
            self.thumbnail.setText(f'Tentando fonte alternativa ({self.preview_attempt + 1}/{len(self.preview_urls)})…')
            self._request_next_preview(self.preview_request_generation)
        finally:
            reply.deleteLater()

    def extent_for_item(self, item, destination_crs):
        """Use STAC footprint/bbox rather than XYZ service global bounds."""
        geom = footprint_from_item(item)
        if geom is not None:
            bounds = geom.boundingBox()
        else:
            coords = item.get('bbox')
            if not isinstance(coords, (list, tuple)) or len(coords) < 4:
                raise ValueError('A imagem não possui limites STAC válidos.')
            bounds = QgsRectangle(float(coords[0]), float(coords[1]),
                                  float(coords[-2]), float(coords[-1]))
        if bounds.isNull() or bounds.isEmpty():
            raise ValueError('A imagem não possui extensão geográfica válida.')
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'),
                                           destination_crs, QgsProject.instance())
        return transform.transformBoundingBox(bounds)

    def zoom_selected(self):
        index = self.results.currentRow()
        if index < 0 or index >= len(self.items):
            self.status.setText('Selecione uma imagem na lista de resultados.')
            return
        try:
            canvas = self.plugin.iface.mapCanvas()
            rect = self.extent_for_item(self.items[index], canvas.mapSettings().destinationCrs())
            rect.scale(1.08)
            canvas.setExtent(rect)
            canvas.refresh()
            self.status.setText('Mapa enquadrado nos limites do levantamento STAC. Se a imagem não carregar, verifique a conexão e o servidor de mosaicos.')
        except Exception as exc:
            self.status.setText('Não foi possível enquadrar a imagem: ' + str(exc))

    def add_selected(self):
        index = self.results.currentRow()
        if index < 0 or index >= len(self.items):
            self.status.setText('Selecione uma imagem.')
            return
        item_id = str(self.items[index]['id'])
        url = ITEM_TILES.format(id=quote(item_id, safe=''))
        nodata = self.nodata_mode.currentData()
        if nodata is not None:
            url += '&nodata=' + str(nodata)
        layer = make_xyz_layer(url, 'OAM | ' + item_id[:45])
        if not layer.isValid():
            self.status.setText('QGIS não reconheceu a conexão XYZ.')
            return
        # Native QGIS 'Zoom to Layer' may use the global XYZ extent. Restrict
        # displayed layer extent when supported and provide reliable STAC zoom.
        try:
            bounds_3857 = self.extent_for_item(self.items[index],
                                QgsCoordinateReferenceSystem('EPSG:3857'))
            layer.setExtent(bounds_3857)
        except Exception:
            pass  # QGIS providers can override extents; STAC zoom still works.
        layer.setCustomProperty('oam/item_id', item_id)
        layer.setCustomProperty('oam/bbox', json.dumps(self.items[index].get('bbox')))
        QgsProject.instance().addMapLayer(layer)
        self.zoom_selected()
        self.status.setText('Camada XYZ adicionada. Se os tiles expirarem, abra o GeoTIFF original pelo botão acima ou tente novamente mais tarde.')

    def download_original(self):
        if self.download_reply is not None or self.batch_running:
            self.status.setText('Já existe download ou fila em andamento. Aguarde ou cancele.')
            return
        index = self.results.currentRow()
        if index < 0 or index >= len(self.items):
            self.status.setText('Selecione uma imagem antes de baixar o arquivo original.')
            return
        item = self.items[index]
        assets = item.get('assets') or {}
        visual = assets.get('visual') or {}
        href = visual.get('href')
        if not isinstance(href, str) or not href.startswith('https://'):
            self.status.setText('A imagem selecionada não possui URL HTTPS do arquivo original.')
            return
        url = QUrl(href)
        if not url.isValid() or url.scheme() != 'https':
            self.status.setText('URL HTTPS inválida no catálogo STAC.')
            return
        filename = filename_for_item(item)
        suggested_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        filepath, _ = QFileDialog.getSaveFileName(self, 'Salvar imagem original',
                        str(Path(suggested_dir) / filename), 'Imagem GeoTIFF (*.tif *.tiff)')
        if not filepath:
            return
        target = Path(filepath)
        if target.suffix.lower() not in ('.tif', '.tiff'):
            target = target.with_suffix('.tif')
        if target.exists():
            response = QMessageBox.question(self, 'Substituir arquivo?',
                    f'O arquivo {target.name} já existe. Deseja substituí-lo após concluir o download?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
            if response != QMessageBox.StandardButton.Yes:
                return
        temp = Path(str(target) + '.part')
        if temp.exists():
            self.status.setText('Existe um arquivo temporário de download com esse nome. Exclua-o ou escolha outro destino.')
            return
        self._start_download_job(item, href, target, batch=False)

    def _start_download_job(self, item, href, target, batch=False):
        target = Path(target)
        temp = Path(str(target) + '.part')
        if temp.exists():
            self.status.setText('Arquivo temporário já existente: ' + temp.name)
            if batch:
                self._batch_job_done(False, 'Arquivo .part existente')
            return
        self.active_batch_job = (str(item.get('id', 'imagem')), target.name) if batch else None
        url = QUrl(href)
        handle = QFile(str(temp))
        if not handle.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.NewOnly):
            self.status.setText('Não foi possível criar arquivo temporário: ' + handle.errorString())
            if batch:
                self._batch_job_done(False, 'Falha ao criar arquivo')
            return
        self.download_file = handle
        self.download_temp_path = temp
        self.download_final_path = target
        self.download_aborted = False
        self.download_error = ''
        self.download_original_url = href
        req = QNetworkRequest(url)
        req.setRawHeader(b'Accept', b'application/octet-stream, image/tiff, */*')
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        try:
            reply = QgsNetworkAccessManager.instance().get(req)
        except Exception as exc:
            self._cleanup_download()
            self.status.setText('Não foi possível iniciar download: ' + str(exc))
            if batch:
                self._batch_job_done(False, str(exc))
            return
        self.download_reply = reply
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.download_progress.setRange(0, 0)
        self.download_progress.setFormat('Aguardando resposta do servidor…')
        self.status.setText('Baixando ' + target.name + '…')
        reply.readyRead.connect(lambda r=reply: self._download_ready(r))
        reply.downloadProgress.connect(lambda received, total, r=reply: self._download_progress(r, received, total))
        reply.finished.connect(lambda r=reply: self._download_finished(r))

    def _download_ready(self, reply):
        if reply is not self.download_reply or self.download_file is None or self.download_error:
            return
        try:
            chunk = bytes(reply.readAll())
            if chunk and self.download_file.write(chunk) != len(chunk):
                self.download_error = 'Falha de gravação: ' + self.download_file.errorString()
                reply.abort()
        except Exception as exc:
            self.download_error = 'Falha ao gravar: ' + str(exc)
            reply.abort()

    def _download_progress(self, reply, received, total):
        if reply is not self.download_reply:
            return
        if total > 0:
            self.download_progress.setRange(0, 100)
            self.download_progress.setValue(min(100, int(received * 100 / total)))
            self.download_progress.setFormat('%%p%% — %.1f / %.1f MB' % (received / 1048576, total / 1048576))
        else:
            self.download_progress.setRange(0, 0)
            self.download_progress.setFormat('Recebidos %.1f MB (tamanho desconhecido)' % (received / 1048576))

    def cancel_download(self):
        if self.download_reply is not None:
            self.download_aborted = True
            self.status.setText('Cancelando download…')
            self.download_reply.abort()

    def _cleanup_download(self):
        if self.download_file is not None:
            try:
                self.download_file.close()
            except Exception:
                pass
            self.download_file = None
        if self.download_temp_path is not None:
            try:
                self.download_temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        self.download_temp_path = None
        self.download_final_path = None
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _download_finished(self, reply):
        if reply is not self.download_reply:
            reply.deleteLater()
            return
        self._download_ready(reply)
        self.download_reply = None
        http = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        try:
            status = int(http) if http is not None else None
        except (ValueError, TypeError):
            status = None
        error = reply.error()
        transferred = self.download_file.size() if self.download_file is not None else 0
        file_path = self.download_final_path
        tmp_path = self.download_temp_path
        failure = self.download_error
        if self.download_aborted:
            failure = 'Download cancelado pelo usuário.'
        elif status is None or not 200 <= status < 300:
            failure = f'Servidor retornou HTTP {status if status is not None else "desconhecido"}: {reply.errorString()}'
        elif error != QNetworkReply.NetworkError.NoError:
            failure = 'Falha de rede: ' + reply.errorString()
        elif transferred == 0:
            failure = 'O servidor retornou um arquivo vazio.'
        if self.download_file is not None:
            if not self.download_file.flush():
                failure = failure or 'Falha ao descarregar dados para o disco.'
            self.download_file.close()
            self.download_file = None
        if not failure and tmp_path is not None:
            # Reject HTML/JSON error pages incorrectly served with HTTP 200.
            with open(tmp_path, 'rb') as check:
                signature = check.read(4)
            if signature not in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'):
                failure = 'O conteúdo recebido não possui assinatura TIFF válida.'
        if not failure and tmp_path is not None and file_path is not None:
            try:
                os.replace(tmp_path, file_path)
            except OSError as exc:
                failure = 'Não foi possível finalizar o arquivo: ' + str(exc)
        if failure:
            self._cleanup_download()
            self.download_progress.setRange(0, 100)
            self.download_progress.setValue(0)
            self.download_progress.setFormat('Download não concluído')
            self.status.setText(failure)
        else:
            self.download_temp_path = None
            self.download_final_path = None
            self.download_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.download_progress.setRange(0, 100)
            self.download_progress.setValue(100)
            self.download_progress.setFormat('Concluído — %.1f MB' % (transferred / 1048576))
            self.status.setText('Download concluído: ' + str(file_path))
            if (self.batch_add_layers if self.active_batch_job is not None else self.add_after_download.isChecked()):
                layer = QgsRasterLayer(str(file_path), file_path.stem, 'gdal')
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.status.setText('GeoTIFF baixado e adicionado ao projeto: ' + file_path.name)
                else:
                    self.status.setText('Download concluído, mas o GeoTIFF não pôde ser aberto pelo GDAL: ' + str(file_path))
        reply.deleteLater()
        if self.active_batch_job is not None:
            self._batch_job_done(not bool(failure), failure or 'Concluído')

    def _history_add(self, label):
        self.batch_history_list.addItem(label)
        self.batch_history_list.scrollToBottom()

    def download_batch(self):
        if self.download_reply is not None or self.batch_running:
            self.status.setText('Conclua primeiro o download em andamento.')
            return
        indices = sorted({self.results.row(entry) for entry in self.results.selectedItems()})
        indices = [idx for idx in indices if 0 <= idx < len(self.items)]
        if not indices:
            self.status.setText('Selecione uma ou mais imagens usando Ctrl/Shift na lista.')
            return
        folder = QFileDialog.getExistingDirectory(self, 'Pasta para os GeoTIFFs da fila',
                  QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home()))
        if not folder:
            return
        root = Path(folder)
        reserved = set()
        jobs = []
        for idx in indices:
            item = self.items[idx]
            href = ((item.get('assets') or {}).get('visual') or {}).get('href')
            if not isinstance(href, str) or not href.startswith('https://'):
                self._history_add('Ignorado | Sem GeoTIFF HTTPS | ' + str(item.get('id', 'imagem')))
                continue
            base = Path(filename_for_item(item)).stem
            target = root / (base + '.tif')
            n = 2
            while target.exists() or Path(str(target) + '.part').exists() or str(target).lower() in reserved:
                target = root / (base + '_' + str(n) + '.tif')
                n += 1
            reserved.add(str(target).lower())
            jobs.append((item, href, target))
            self._history_add('Na fila | ' + target.name)
        if not jobs:
            self.status.setText('Nenhuma imagem com GeoTIFF disponível para download.')
            return
        self.batch_queue = jobs
        self.batch_running = True
        self.batch_cancel_requested = False
        self.batch_add_layers = self.add_after_download.isChecked()
        self.batch_button.setEnabled(False)
        self.cancel_queue_button.setEnabled(True)
        self._start_next_batch()

    def _start_next_batch(self):
        if not self.batch_running:
            return
        if self.batch_cancel_requested or not self.batch_queue:
            self.batch_queue.clear()
            self.batch_running = False
            self.active_batch_job = None
            self.batch_button.setEnabled(True)
            self.cancel_queue_button.setEnabled(False)
            self.status.setText('Fila finalizada. Consulte o histórico de transferências.')
            return
        item, href, target = self.batch_queue.pop(0)
        self._history_add('Iniciando | ' + target.name)
        self._start_download_job(item, href, target, batch=True)

    def _batch_job_done(self, success, message):
        job = self.active_batch_job
        self.active_batch_job = None
        if job is not None:
            self._history_add(('Concluído' if success else 'Erro/cancelado') + ' | ' + job[1] + (' | ' + message if not success else ''))
        QTimer.singleShot(0, self._start_next_batch)

    def cancel_batch(self):
        if not self.batch_running:
            return
        self.batch_cancel_requested = True
        for _, _, target in self.batch_queue:
            self._history_add('Cancelado (fila) | ' + target.name)
        self.batch_queue.clear()
        if self.download_reply is not None:
            self.cancel_download()
        else:
            self._start_next_batch()

    def show_coverages(self):
        if not self.items:
            self.status.setText('Pesquise imagens antes de exibir coberturas.')
            return
        layer = QgsVectorLayer('MultiPolygon?crs=EPSG:4326', 'OAM | Coberturas da pesquisa', 'memory')
        if not layer.isValid():
            self.status.setText('Falha ao criar camada de coberturas.')
            return
        from qgis.PyQt.QtCore import QVariant
        layer.dataProvider().addAttributes([QgsField('item_id', QVariant.String),
                                           QgsField('data', QVariant.String),
                                           QgsField('gsd_m', QVariant.Double),
                                           QgsField('plataforma', QVariant.String)])
        layer.updateFields()
        features = []
        for item in self.items:
            geom = footprint_from_item(item)
            if geom is None:
                continue
            feat = QgsFeature(layer.fields())
            if not geom.isMultipart():
                geom.convertToMultiType()
            feat.setGeometry(geom)
            p = item.get('properties') or {}
            gsd = p.get('gsd')
            try:
                gsd = float(gsd) if gsd is not None else None
            except (ValueError, TypeError):
                gsd = None
            feat.setAttributes([str(item['id']), valid_date(p.get('datetime') or p.get('start_datetime')),
                                gsd, str(p.get('oam:platform_type') or '')])
            features.append(feat)
        if not features:
            self.status.setText('Nenhuma imagem retornou geometria poligonal válida.')
            return
        layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        symbol = QgsFillSymbol.createSimple({'style': 'no', 'outline_style': 'solid',
                                             'outline_color': '255,140,20,255', 'outline_width': '0.5'})
        layer.renderer().setSymbol(symbol)
        QgsProject.instance().addMapLayer(layer)
        self.status.setText(f'{len(features)} cobertura(s) adicionada(s). Os polígonos usam as geometrias STAC.')

    def closeEvent(self, event):
        if self.download_reply is not None or self.batch_running:
            QMessageBox.information(self, 'Download em andamento',
                 'Conclua ou cancele o download antes de fechar esta janela.')
            event.ignore()
            return
        event.accept()


class OpenAerialMapExplorer:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None

    def initGui(self):
        self.action = QAction(QIcon(str(Path(__file__).resolve().parent / 'icon.png')),
                              'OpenAerialMap Explorer', self.iface.mainWindow())
        self.action.setObjectName('OpenAerialMapExplorerAction')
        self.action.setToolTip('Explorar imagens abertas do OpenAerialMap')
        self.action.triggered.connect(self.show_dock)
        self.iface.addPluginToRasterMenu('OpenAerialMap Explorer', self.action)
        self.iface.addToolBarIcon(self.action)

    def show_dock(self):
        if self.dock is None:
            self.dock = OAMWindow(self)
        self.dock.show()
        self.dock.center_on_screen()
        self.dock.raise_()
        self.dock.activateWindow()
        self._show_imagery_notice()
        # QGIS's main status bar must remain visible after docking.
        bar = self.iface.mainWindow().statusBar()
        if bar is not None and not bar.isVisible():
            bar.show()

    def _show_imagery_notice(self):
        """Inform users about spatial and temporal limitations of third-party imagery."""
        key = 'OpenAerialMapExplorer/hideImageryNotice'
        settings = QSettings()
        if settings.value(key, False, type=bool):
            return
        msg = QMessageBox(self.dock)
        msg.setWindowTitle('Aviso técnico — OpenAerialMap Explorer')
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText('Atenção à qualidade geométrica, posicional e temporal das imagens')
        msg.setInformativeText(
            'As imagens disponibilizadas pelo OpenAerialMap são produzidas por terceiros '
            'e podem apresentar distorções geométricas, imprecisão posicional, '
            'efeitos de paralaxe e diferenças de datas de aquisição (anacronismo temporal).\n\n'
            'A sobreposição com outras camadas não garante coincidência espacial. '
            'A data exibida nos metadados deve ser conferida antes de comparações temporais.\n\n'
            'Antes do uso científico, técnico ou decisório, verifique procedência, '
            'resolução, sistema de referência, georreferenciamento, precisão e '
            'data de aquisição. Sempre consulte os metadados e a licença de cada imagem.\n\n'
        )
        check = QCheckBox('Não mostrar este aviso novamente')
        msg.setCheckBox(check)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg.exec()
        if check.isChecked():
            settings.setValue(key, True)

    def add_basemap(self):
        name = 'OpenAerialMap | Mosaico global'
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == name:
                if self.dock:
                    self.dock.status.setText('O mapa-base já está no projeto.')
                return
        layer = make_xyz_layer(BASEMAP, name)
        if not layer.isValid():
            QMessageBox.warning(self.iface.mainWindow(), 'OpenAerialMap Explorer',
                                'Não foi possível criar a camada XYZ.')
            return
        QgsProject.instance().addMapLayer(layer)
        if self.dock:
            self.dock.status.setText('Mapa-base adicionado. Aproximar para zoom 14+ para ver imagens.')

    def unload(self):
        if self.dock:
            for reply in (self.dock.reply, self.dock.thumbnail_reply):
                if reply is not None:
                    try:
                        reply.abort()
                    except RuntimeError:
                        pass
            if self.dock.download_reply is not None:
                self.dock.cancel_batch()
                self.dock.cancel_download()
            self.dock.close()
            self.dock.deleteLater()
            self.dock = None
        if self.action:
            self.iface.removePluginRasterMenu('OpenAerialMap Explorer', self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
